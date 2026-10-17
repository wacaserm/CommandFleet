# CommandFleet: State and Score Broadcast Sequence

**Student:** Madison Wacaser  
**Course:** CS 457 - Computer Networks  
**Deliverable:** Sprint 2  
**Architecture:** [concurrency_design.md](concurrency_design.md)

## 1. Valid move, error branches, and round completion

This example begins after both clients have joined and `GAME_START` has finished. Player 1 is initially active. All requests use the LF-terminated JSON protocol in `protocol_blueprint.md`. The server monitors both sockets throughout play, including the inactive player's socket.

```mermaid
sequenceDiagram
    autonumber
    participant P1 as Client 1 (Player_1)
    participant S as Server event loop
    participant P2 as Client 2 (Player_2)

    P2->>S: MOVE while Player_1 is active
    S->>S: Check socket identity and active turn
    S-->>P2: ERROR NOT_YOUR_TURN
    Note over S: Board, turn, and scores unchanged

    P1->>S: MOVE with row and col
    S->>S: Validate schema, game state, active turn, and coordinate
    alt Malformed, out of range, or already attacked
        S-->>P1: ERROR with applicable code
        Note over S: Player_1 keeps the turn; no state or score changes
    else Legal active-player move
        S->>S: Record attack and calculate hit, miss, or sunk ship
        S->>S: Check whether opponent fleet is fully sunk
        alt Opponent still has ships
            S->>S: Set active_player to Player_2
            S->>S: Build both private views and queue both updates
            Note over S: One committed board, turn, and score snapshot
            par Independent socket delivery to Player_1
                S-->>P1: STATE_UPDATE with P1 view, Player_2 turn, scores
            and Independent socket delivery to Player_2
                S-->>P2: STATE_UPDATE with P2 view, Player_2 turn, same scores
            end
        else Opponent fleet is fully sunk
            S->>S: Enter GAME_OVER and set active_player to null
            S->>S: Record P1 win, P2 loss, and round points once
            S->>S: Build both final views and queue both updates
            par Independent socket delivery to Player_1
                S-->>P1: STATE_UPDATE with final P1 view and updated scores
                S-->>P1: GAME_OVER fleet_sunk, winner Player_1, scores
            and Independent socket delivery to Player_2
                S-->>P2: STATE_UPDATE with final P2 view and same updated scores
                S-->>P2: GAME_OVER fleet_sunk, winner Player_1, same scores
            end
            S->>S: Drain pending final messages through write-ready events
            S->>S: CLEANUP closes match sockets and clears round state
            Note over S: Preserve leaderboard and listener; return to WAITING_FOR_PLAYERS
        end
    end
```

The server checks each request against the current state when that request is dispatched. Invalid actions produce an error only to the sender, with no turn or score changes. For a valid nonterminal attack, the server applies the result and switches the turn before building either update. Both clients therefore receive the same new active player and score table.

If the attack wins the round, there is no next turn. The server awards one point to the winner, records the win/loss, and includes those totals in both final `STATE_UPDATE` and `GAME_OVER`. Each connection's FIFO output queue preserves that order. On the client, receiving `active_player=null` prevents another attack while `GAME_OVER` is being delivered.

The arrows represent logical message order. The `par` branches show independent delivery on the two sockets; they do not mean that server callbacks run in multiple threads. Actual writes run only when the corresponding socket is writable, and messages on different connections can arrive at different times. Both recipient-specific messages are queued from the same committed state before another move handler executes. That is the synchronization guarantee; the server does not block waiting for one client's display or acknowledgement.

## 2. Mid-game disconnect and scoring

```mermaid
sequenceDiagram
    autonumber
    participant P1 as Client 1 (Player_1)
    participant S as Server event loop
    participant P2 as Client 2 (Player_2)

    alt Intentional departure
        P2->>S: DISCONNECT with reason quit
    else Transport failure
        Note over P2,S: Server detects EOF, reset, or another socket failure
    end
    S->>S: Mark match terminal and stop accepting moves
    S->>S: Record P1 forfeit win and P2 loss exactly once
    S-->>P1: GAME_OVER forfeit, winner Player_1, final state and scores
    S->>S: Flush reachable client's final output or reach close deadline
    S->>S: Unregister and close match sockets; clear round state
    Note over S: Keep leaderboard and listener for the next match
```

Before setup completes, a departure instead produces `abandoned`, `winner=null`, and `final_state=null`, without awarding points. The server sends the result only to connections it can still reach. A later write failure or EOF cannot score the same round again because `result_recorded` is already set. After cleanup, two new connections can join a fresh match while previous alias totals remain in the leaderboard.
