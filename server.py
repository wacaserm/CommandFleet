"""Run the authoritative CommandFleet game on a non-blocking TCP server."""

import argparse
from dataclasses import dataclass, field
from enum import Enum
import random
import selectors
import socket
import sys
import time

from protocol import (
    BOARD_SIZE,
    ProtocolError,
    encode,
    pop_message,
    port_number,
    valid_alias,
)

FLEET = (
    ("carrier", 5), ("battleship", 4), ("cruiser", 3),
    ("submarine", 3), ("destroyer", 2),
)
PLAYER_IDS = ("Player_1", "Player_2")
JOIN_TIMEOUT = 20
FLUSH_TIMEOUT = 3
MAX_PENDING_OUTPUT = 65536
MAX_PEERS = 32
# Bound the leaderboard so every complete score snapshot fits in one frame.
MAX_ALIASES = 64


class State(Enum):
    INIT = "INIT"
    WAITING = "WAITING_FOR_PLAYERS"
    START = "GAME_START"
    TURN = "PLAYER_TURN"
    EVALUATE = "EVALUATE_MOVE"
    CHECK_WIN = "CHECK_WIN"
    OVER = "GAME_OVER"
    CLEANUP = "CLEANUP"


@dataclass
class Peer:
    sock: socket.socket
    incoming: bytearray = field(default_factory=bytearray)
    outgoing: bytearray = field(default_factory=bytearray)
    player_id: str = ""
    alias: str = ""
    join_by: float = field(
        default_factory=lambda: time.monotonic() + JOIN_TIMEOUT
    )
    close_by: float = 0.0
    failed: bool = False


def make_fleet():
    """Choose from legal placements for each ship; ships may touch."""
    fleet = {}
    occupied = set()
    for name, length in FLEET:
        choices = []
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                for dr, dc in ((0, 1), (1, 0)):
                    last = (row + dr * (length - 1), col + dc * (length - 1))
                    if max(last) >= BOARD_SIZE:
                        continue
                    cells = {
                        (row + dr * i, col + dc * i) for i in range(length)
                    }
                    if not occupied & cells:
                        choices.append(cells)
        fleet[name] = random.choice(choices)
        occupied.update(fleet[name])
    return fleet


def board_view(fleet, shots, hidden):
    occupied = set().union(*fleet.values())

    def symbol(cell):
        if cell in shots:
            return "X" if cell in occupied else "o"
        if hidden:
            return "?"
        return "S" if cell in occupied else "."

    return {
        "size": BOARD_SIZE,
        "cells": [
            "".join(symbol((row, col)) for col in range(BOARD_SIZE))
            for row in range(BOARD_SIZE)
        ],
    }


class GameServer:
    """One selector loop owns all sockets, fleets, turns, and scores."""

    def __init__(self):
        self.selector = selectors.DefaultSelector()
        self.peers = {}
        self.players = {}
        self.fleets = {}
        self.attacks = {}
        self.leaderboard = {}
        self.points = {}
        self.active = None
        self.last_move = None
        self.turns = 0
        self.state = State.INIT

    def transition(self, state):
        if state is not self.state:
            print(f"{self.state.value} -> {state.value}", flush=True)
            self.state = state

    def accept(self, listener):
        try:
            sock, _ = listener.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)
        if len(self.peers) >= MAX_PEERS:
            sock.close()
            return
        peer = Peer(sock)
        self.peers[sock] = peer
        self.selector.register(sock, selectors.EVENT_READ, peer)
        if len(self.players) == 2 or self.state is State.OVER:
            self.error(peer, "ROOM_FULL", "A match already occupies the room.")
            self.drain_then_close(peer)

    def interest(self, peer):
        events = 0 if peer.close_by else selectors.EVENT_READ
        if peer.outgoing:
            events |= selectors.EVENT_WRITE
        if events:
            self.selector.modify(peer.sock, events, peer)

    def queue(self, peer, kind, **payload):
        if peer.sock not in self.peers or peer.failed:
            return
        message = encode(kind, **payload)
        if len(peer.outgoing) + len(message) > MAX_PENDING_OUTPUT:
            peer.failed = True
            return
        peer.outgoing.extend(message)
        self.interest(peer)

    def error(self, peer, code, message):
        self.queue(peer, "ERROR", code=code, message=message)

    def drain_then_close(self, peer):
        if peer.sock not in self.peers:
            return
        if not peer.outgoing or peer.failed:
            self.close(peer)
            return
        peer.close_by = time.monotonic() + FLUSH_TIMEOUT
        self.interest(peer)

    def close(self, peer):
        if self.peers.pop(peer.sock, None) is not None:
            self.selector.unregister(peer.sock)
            peer.sock.close()

    def depart(self, peer, *, flush_error=False):
        if flush_error:
            self.drain_then_close(peer)
        else:
            self.close(peer)
        if not peer.player_id or self.state is State.OVER:
            return
        if self.state is State.TURN:
            other = next(pid for pid in self.players if pid != peer.player_id)
            self.finish("forfeit", other, f"{peer.alias} disconnected", peer)
        else:
            self.finish(
                "abandoned", None, f"{peer.alias} left the lobby", peer
            )

    def read(self, peer):
        # Dispatch buffered frames before reading more, including before EOF.
        if peer.close_by or b"\n" in peer.incoming:
            return
        try:
            data = peer.sock.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            self.depart(peer)
            return
        if not data:
            self.depart(peer)
        else:
            peer.incoming.extend(data)

    def write(self, peer):
        if not peer.outgoing:
            return
        try:
            sent = peer.sock.send(peer.outgoing)
        except BlockingIOError:
            return
        except OSError:
            self.depart(peer)
            return
        if sent == 0:
            self.depart(peer)
            return
        del peer.outgoing[:sent]
        if peer.close_by and not peer.outgoing:
            self.close(peer)
        else:
            self.interest(peer)

    def connect(self, peer, payload):
        alias = payload.get("alias")
        if (
            peer.player_id
            or set(payload) != {"alias"}
            or not valid_alias(alias)
        ):
            self.error(
                peer, "INVALID_SCHEMA", "Send CONNECT once with a valid alias."
            )
            return
        if self.state is not State.WAITING or len(self.players) >= 2:
            self.error(peer, "ROOM_FULL", "A match already occupies the room.")
            self.drain_then_close(peer)
            return
        if any(other.alias == alias for other in self.players.values()):
            self.error(peer, "ALIAS_TAKEN", "Choose a different alias.")
            self.drain_then_close(peer)
            return
        if (
            alias not in self.leaderboard
            and len(self.leaderboard) >= MAX_ALIASES
        ):
            self.error(
                peer, "ROOM_FULL", "Leaderboard is full for new aliases."
            )
            self.drain_then_close(peer)
            return
        peer.player_id = PLAYER_IDS[len(self.players)]
        peer.alias = alias
        self.players[peer.player_id] = peer
        self.points[peer.player_id] = 0
        self.leaderboard.setdefault(
            alias, {"wins": 0, "losses": 0, "points": 0}
        )
        print(f"{peer.player_id} connected as {alias}", flush=True)
        if len(self.players) == 1:
            self.queue(
                peer, "LOBBY_WAIT", player_id=peer.player_id,
                message="Waiting for Player 2...",
            )
        else:
            self.start_match()

    def start_match(self):
        self.transition(State.START)
        self.fleets = {pid: make_fleet() for pid in self.players}
        self.attacks = {pid: set() for pid in self.players}
        self.active = random.choice(PLAYER_IDS)
        for pid, peer in self.players.items():
            other = self.players[self.opponent(pid)]
            self.queue(
                peer, "GAME_START", player_id=pid, opponent_alias=other.alias,
                active_player=self.active,
            )
        self.broadcast_update()
        self.transition(State.TURN)

    @staticmethod
    def opponent(pid):
        return PLAYER_IDS[1] if pid == PLAYER_IDS[0] else PLAYER_IDS[0]

    def remaining(self, pid):
        shots = self.attacks[self.opponent(pid)]
        return sum(not cells <= shots for cells in self.fleets[pid].values())

    def view(self, pid):
        other = self.opponent(pid)
        return {
            "your_board": board_view(
                self.fleets[pid], self.attacks[other], False
            ),
            "opponent_board": board_view(
                self.fleets[other], self.attacks[pid], True
            ),
            "your_ships_remaining": self.remaining(pid),
            "opponent_ships_remaining": self.remaining(other),
        }

    def scores(self):
        return {
            "round_scores": [
                {
                    "player_id": pid, "alias": peer.alias,
                    "points": self.points[pid],
                }
                for pid, peer in sorted(self.players.items())
            ],
            "leaderboard": [
                {"alias": alias, **stats}
                for alias, stats in sorted(
                    self.leaderboard.items(),
                    key=lambda item: (-item[1]["points"], item[0]),
                )
            ],
        }

    def broadcast_update(self):
        scores = self.scores()
        for pid, peer in self.players.items():
            self.queue(
                peer, "STATE_UPDATE", active_player=self.active,
                last_move=self.last_move, scores=scores, **self.view(pid),
            )

    def move(self, peer, payload):
        if set(payload) != {"row", "col"}:
            self.error(peer, "INVALID_SCHEMA", "MOVE requires row and col.")
            return
        if self.state is not State.TURN:
            self.error(peer, "NOT_READY", "Wait until the match starts.")
            return
        pid = peer.player_id
        if pid != self.active:
            self.error(peer, "NOT_YOUR_TURN", "Wait for your turn.")
            return
        cell = payload["row"], payload["col"]
        if any(type(n) is not int or not 0 <= n < BOARD_SIZE for n in cell):
            self.error(
                peer, "INVALID_COORDINATE", "Use integers from 0 through 9."
            )
            return
        shots = self.attacks[pid]
        if cell in shots:
            self.error(
                peer, "ALREADY_ATTACKED", "You already attacked that cell."
            )
            return

        # Process the entire turn before another peer's message can run.
        self.transition(State.EVALUATE)
        shots.add(cell)
        other = self.opponent(pid)
        target = next(
            ((name, cells) for name, cells in self.fleets[other].items()
             if cell in cells), None,
        )
        outcome = "hit" if target else "miss"
        sunk = target[0] if target and target[1] <= shots else None
        self.last_move = {
            "by": pid, "row": cell[0], "col": cell[1],
            "outcome": outcome, "sunk_ship": sunk,
        }
        self.turns += 1
        print(
            f"Turn {self.turns}: {pid} attacked {cell}: {outcome}", flush=True
        )
        self.transition(State.CHECK_WIN)
        if self.remaining(other) == 0:
            self.finish("fleet_sunk", pid, "Opponent fleet sunk.")
        else:
            self.active = other
            self.broadcast_update()
            self.transition(State.TURN)

    def finish(self, result, winner, reason, departing=None):
        # OVER guards against duplicate scoring on EOF or failed final writes.
        if self.state is State.OVER:
            return
        self.transition(State.OVER)
        self.active = None
        if winner is not None:
            self.points[winner] = 1
            stats = self.leaderboard[self.players[winner].alias]
            stats["wins"] += 1
            stats["points"] += 1
            loser = self.opponent(winner)
            self.leaderboard[self.players[loser].alias]["losses"] += 1
        if result == "fleet_sunk":
            self.broadcast_update()
        for pid, peer in self.players.items():
            if peer is departing:
                continue
            self.queue(
                peer, "GAME_OVER", result=result, winner=winner, reason=reason,
                final_state=self.view(pid) if winner is not None else None,
                scores=self.scores(),
            )
            self.drain_then_close(peer)
        print(f"Result: {result}; winner: {winner}", flush=True)

    def dispatch(self, peer):
        try:
            message = pop_message(peer.incoming)
        except ProtocolError as error:
            self.error(peer, error.code, str(error))
            if error.code == "MESSAGE_TOO_LARGE":
                self.depart(peer, flush_error=True)
            return
        if message is None:
            return
        kind, payload = message
        if kind == "CONNECT":
            self.connect(peer, payload)
        elif kind == "MOVE" and (
            peer.player_id or self.state is State.WAITING
        ):
            self.move(peer, payload)
        elif kind == "DISCONNECT" and payload == {"reason": "quit"}:
            self.depart(peer)
        else:
            self.error(
                peer, "INVALID_SCHEMA", "Unexpected message or payload."
            )

    def service_deadlines(self):
        now = time.monotonic()
        for peer in list(self.peers.values()):
            if peer.failed:
                self.depart(peer)
            elif peer.close_by and now >= peer.close_by:
                self.close(peer)
            elif (
                not peer.player_id
                and not peer.close_by
                and now >= peer.join_by
            ):
                self.close(peer)
        if self.state is State.OVER and not any(
            peer.sock in self.peers for peer in self.players.values()
        ):
            self.transition(State.CLEANUP)
            self.players.clear()
            self.fleets.clear()
            self.attacks.clear()
            self.points.clear()
            self.last_move = None
            self.turns = 0
            self.transition(State.WAITING)

    def run(self, listener):
        listener.setblocking(False)
        self.selector.register(listener, selectors.EVENT_READ)
        self.transition(State.WAITING)
        while True:
            # Complete buffered messages can run without new socket I/O.
            pending = any(
                not peer.close_by and b"\n" in peer.incoming
                for peer in self.peers.values()
            )
            for key, events in self.selector.select(0 if pending else 0.25):
                peer = key.data
                if peer is None:
                    self.accept(listener)
                elif peer.sock in self.peers:
                    if events & selectors.EVENT_READ:
                        self.read(peer)
                    if (
                        peer.sock in self.peers
                        and events & selectors.EVENT_WRITE
                    ):
                        self.write(peer)
            for peer in list(self.peers.values()):
                if (
                    peer.sock in self.peers
                    and not peer.close_by
                    and not peer.failed
                ):
                    self.dispatch(peer)
            self.service_deadlines()

    def shutdown(self):
        for peer in list(self.peers.values()):
            self.close(peer)
        self.selector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--port", required=True, type=port_number)
    args = parser.parse_args()
    game = GameServer()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", args.port))
            listener.listen()
            print(f"Listening on 0.0.0.0:{args.port}", flush=True)
            game.run(listener)
    except KeyboardInterrupt:
        print("Server stopped.")
    except OSError as error:
        print(f"[ERROR] Server: {error}", file=sys.stderr)
        return 1
    finally:
        game.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
