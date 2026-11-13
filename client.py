"""Join CommandFleet and play from a terminal."""

import argparse
from queue import Empty, Queue
import select
import socket
import sys
import threading

from protocol import (
    BOARD_SIZE,
    ProtocolError,
    encode,
    pop_message,
    port_number,
    valid_alias,
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-i", "--hostname", required=True, help="server hostname"
    )
    parser.add_argument("-p", "--port", required=True, type=port_number)
    parser.add_argument("--alias", help="player name; prompted if omitted")
    args = parser.parse_args()
    if args.alias is not None and not valid_alias(args.alias):
        parser.error("alias must use 1–20 ASCII letters, digits, _ or -")
    return args


def read_commands(commands):
    """Allow the socket loop to receive updates while stdin is waiting."""
    for line in sys.stdin:
        commands.put(line.strip())
    commands.put(None)


def show_boards(view):
    print("\nYour fleet                 Opponent waters")
    columns = " ".join(str(n) for n in range(BOARD_SIZE))
    print(f"  {columns}      {columns}")
    own = view["your_board"]["cells"]
    other = view["opponent_board"]["cells"]
    for row, (left, right) in enumerate(zip(own, other)):
        print(f"{row} {' '.join(left)}    {row} {' '.join(right)}")
    print("S ship  X hit  o miss  . empty  ? unknown")
    print(
        f"Ships remaining: you {view['your_ships_remaining']}, "
        f"opponent {view['opponent_ships_remaining']}"
    )


def show_scores(scores):
    points = ", ".join(
        f"{row['alias']} {row['points']}" for row in scores["round_scores"]
    )
    print(f"Round points: {points}")
    print("Leaderboard (points / wins / losses):")
    for row in scores["leaderboard"]:
        print(
            f"  {row['alias']}: {row['points']} / "
            f"{row['wins']} / {row['losses']}"
        )


class ClientGame:
    def __init__(self, sock):
        self.sock = sock
        self.player_id = None
        self.opponent = None
        self.active = None
        self.awaiting_move = False
        self.incoming = bytearray()

    def your_turn(self):
        return self.player_id is not None and self.active == self.player_id

    def show_turn(self):
        if self.your_turn():
            print("Your Turn — enter row col (0–9), or quit.", flush=True)
        elif self.active is not None:
            print(f"Waiting for {self.opponent}...", flush=True)

    def receive(self, kind, payload):
        if kind == "LOBBY_WAIT":
            self.player_id = payload["player_id"]
            print(payload["message"], flush=True)
        elif kind == "GAME_START":
            self.player_id = payload["player_id"]
            self.opponent = payload["opponent_alias"]
            print(
                f"Match started: you are {self.player_id} vs {self.opponent}."
            )
        elif kind == "STATE_UPDATE":
            self.active = payload["active_player"]
            self.awaiting_move = False
            move = payload["last_move"]
            if move:
                print(
                    f"{move['by']} attacked ({move['row']}, {move['col']}): "
                    f"{move['outcome']}."
                )
                if move["sunk_ship"]:
                    print(f"Sunk ship: {move['sunk_ship']}.")
            show_boards(payload)
            show_scores(payload["scores"])
            self.show_turn()
        elif kind == "ERROR":
            self.awaiting_move = False
            code = payload["code"]
            print(f"[ERROR] {code}: {payload['message']}", flush=True)
            if code in {"ROOM_FULL", "ALIAS_TAKEN", "MESSAGE_TOO_LARGE"}:
                return 1
            self.show_turn()
        elif kind == "GAME_OVER":
            if payload["final_state"] and payload["result"] == "forfeit":
                show_boards(payload["final_state"])
            print(f"Game over ({payload['result']}): {payload['reason']}")
            winner = payload["winner"]
            if winner is not None:
                print("You won!" if winner == self.player_id else "You lost.")
            show_scores(payload["scores"])
            return 0
        return None

    def send_move(self, line):
        if not self.your_turn() or self.awaiting_move:
            print("Wait for your next turn, or enter quit.", flush=True)
            return
        try:
            row, col = map(int, line.split())
            if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
                raise ValueError
        except ValueError:
            print("Enter two integers from 0 through 9, e.g. 3 7.", flush=True)
            return
        self.sock.sendall(encode("MOVE", row=row, col=col))
        self.awaiting_move = True

    def play(self, alias):
        self.sock.sendall(encode("CONNECT", alias=alias))
        commands = Queue(maxsize=32)
        threading.Thread(
            target=read_commands, args=(commands,), daemon=True
        ).start()
        while True:
            # Windows select() supports sockets, but not terminal input.
            readable, _, _ = select.select([self.sock], [], [], 0.1)
            if readable:
                data = self.sock.recv(4096)
                if not data:
                    print(
                        "[ERROR] Server closed the connection.",
                        file=sys.stderr,
                    )
                    return 1
                self.incoming.extend(data)
                while (message := pop_message(self.incoming)) is not None:
                    status = self.receive(*message)
                    if status is not None:
                        return status

            try:
                command = commands.get_nowait()
            except Empty:
                continue
            if command is None or command.lower() in {"q", "quit"}:
                self.quit()
                return 0
            self.send_move(command)

    def quit(self):
        try:
            self.sock.sendall(encode("DISCONNECT", reason="quit"))
        except OSError:
            pass


def main():
    args = parse_args()
    try:
        server_ip = socket.gethostbyname(args.hostname)
    except socket.gaierror:
        print(
            f"[ERROR] Unable to resolve DNS hostname '{args.hostname}'.",
            file=sys.stderr,
        )
        print(
            "Check the Cisco router DNS configuration ('ip dns server' / "
            "'ip host') or verify the hostname spelling.",
            file=sys.stderr,
        )
        return 1

    try:
        alias = args.alias
        while alias is None:
            entered = input("Player alias: ").strip()
            if valid_alias(entered):
                alias = entered
            else:
                print("Use 1–20 ASCII letters, digits, _ or -.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect((server_ip, args.port))
            print(
                f"Connected to {args.hostname}:{args.port} as {alias}.",
                flush=True,
            )
            game = ClientGame(sock)
            try:
                return game.play(alias)
            except KeyboardInterrupt:
                game.quit()
                print("\nDisconnected.")
                return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 0
    except (ProtocolError, KeyError, TypeError):
        print("[ERROR] Invalid response from the server.", file=sys.stderr)
        return 1
    except OSError as error:
        print(
            f"[ERROR] Connection to {args.hostname}:{args.port}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
