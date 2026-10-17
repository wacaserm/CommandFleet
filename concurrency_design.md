# CommandFleet: Server Concurrency and Game Behavior Design

**Student:** Madison Wacaser  
**Course:** CS 457 - Computer Networks  
**Deliverable:** Sprint 2  
**Related files:** [Protocol blueprint](protocol_blueprint.md), [FSM](fsm_specification.md), [sequence diagrams](sequence_diagram.md)

## 1. Concurrency model

I will use **non-blocking I/O multiplexing with `selectors.DefaultSelector`**. One server thread will monitor the listening socket and both client sockets. Each socket will use `setblocking(False)`. The selector reports when a socket can be read from or written to, so the server can service either player without waiting for the other player to type a move. This fits a two-player turn-based game because each operation is small: validate a coordinate, update a board, and queue messages.

The server supports one match of two registered players at a time. The listening socket remains open during a match and after it ends. Additional connections receive `ERROR ROOM_FULL` and are closed after that response is sent or the close deadline expires. New matches use new client connections after the room resets.

This design uses no worker threads and no `threading.Lock`. Concurrency comes from monitoring several sockets; game logic still runs one event at a time. The selector may wait while **all** sockets are idle. That is different from waiting on one player's blocking `recv()` while another player's data is available.

## 2. Complete connection lifecycle

| Stage | Server behavior |
| --- | --- |
| Listen | Create the TCP socket, bind port `45700`, call `listen()`, set it non-blocking, and register `EVENT_READ` with the selector. |
| Accept | When the listener is readable, accept a connection, explicitly set the new socket non-blocking, allocate its receive buffer and output queue, and register it for `EVENT_READ`. |
| Register player | Require a valid `CONNECT` before game commands. Validate the alias and room capacity. Associate the socket with a server-assigned player ID. First player receives `LOBBY_WAIT`; second player triggers `GAME_START`. Duplicate aliases receive `ALIAS_TAKEN`. Invalid or repeated `CONNECT` receives `INVALID_SCHEMA`. |
| Start match | In the `GAME_START` state, generate both fleets, randomly choose the starting player, and initialize round scores to zero. Queue each client's `GAME_START`, then its initial `STATE_UPDATE`. Both updates use the same committed turn and score table, with different private board views. |
| Service sockets | Read and write only when the corresponding readiness event is reported. Receive data from either player, extract complete messages, and dispatch them through the server FSM. |
| End match | A fleet victory or detected mid-game departure makes the result terminal. Update scores once and queue the appropriate final messages. New moves cannot change this result. |
| Drain and close | Keep write events enabled until final output is sent. Then unregister each match socket before closing it. If a connection fails or a configured final-flush deadline expires, close it without waiting indefinitely. |
| Reset | Clear player registrations, boards, attacks, round scores, and active turn. Keep the listening socket and accumulated leaderboard, then return to `WAITING_FOR_PLAYERS`. |
| Shutdown | On operator shutdown, unregister and close remaining sockets and close the selector. In-memory leaderboard totals are lost when the process exits. |

Before registration, pending connections do not own a player slot. A configurable registration deadline closes clients that never finish `CONNECT`. Only two clients become game participants; rejected or expired connections never mutate the game state or scores.

## 3. Reading, writing, and keeping the loop responsive

### Receiving

Each client has its own byte buffer. On `EVENT_READ`, append bytes from `recv()` and extract complete LF-terminated JSON messages according to Sprint 1. Keep unfinished bytes for a later read. The existing 8192-byte maximum before LF is a message limit, independent of the size requested from `recv()`.

If one read contains several complete messages, put those messages in that client's pending-message queue. Dispatch a bounded number per client per loop pass, rotating between clients. Pending complete messages remain runnable even if no additional bytes arrive; the server must not wait for another read event to process them. This prevents a burst from one player from starving the other player's connection.

`BlockingIOError` means the operation would currently block: return to the selector, retaining buffers. It is not a disconnect. An empty read (`b""`) is EOF and triggers disconnect handling. Catch resets, broken pipes, and other socket failures without crashing the event loop.

### Sending

Each client has a separate FIFO queue of serialized bytes. Append complete JSON-plus-LF messages and enable `EVENT_WRITE` only while output is pending. On a write event, call non-blocking `send()`, remove only the number of bytes actually sent, and preserve the remainder for the next write event. Disable write monitoring when the queue becomes empty, avoiding an idle busy loop.

**Server output uses queued `send()` rather than a blocking `sendall()` loop.** A client that temporarily cannot receive must not prevent service to the other client. Messages retain their order within each socket even when one message takes several writes. Closing a socket immediately after queueing `GAME_OVER` would discard unsent bytes, so teardown waits for queue drain or the final-flush deadline. Slow connections have bounded output queues; reaching the configured queue limit is treated as a connection failure rather than allowing unlimited memory growth.

No callback waits for user input, performs a blocking network operation, or waits for another client's acknowledgement. The non-blocking receive and send queue handling implements the same framing defined in `protocol_blueprint.md`.

## 4. Shared resources and event loop isolation

| Resource | Purpose | Mutation rule |
| --- | --- | --- |
| `connections` and player registrations | Socket, alias, assigned ID, buffers, and lifecycle flags | Accept, registration, and teardown handlers in the event loop only. |
| `boards` and fleet records | Private placements, hits, and sunk ships | Setup handler creates them; accepted-move handler changes hit state. |
| `attack_history` | Coordinates each player has already targeted | Updated only after all move checks pass. |
| `active_player` and `fsm_state` | Current turn and lifecycle state | Changed only by FSM handlers. |
| `round_scores` | Current match's points for registered players | Initialized at join/start and finalized once at match end. |
| `leaderboard` | Alias-based totals during this server process | Updated by the match-finalization handler only. |
| `result` and `result_recorded` | Terminal result and duplicate-scoring protection | Set once when the match ends; cleared during reset. |
| Per-client output queues | Ordered bytes awaiting transmission | Filled by event handlers and drained by write-ready handlers in the same thread. |

The critical operation is **validate → mutate → check victory → select next turn or finalize result → build both views → queue both views**. The event loop completes that operation before dispatching another client command. It does not yield midway or expose a partly updated board. Outgoing messages are serialized copies, not references to mutable game dictionaries.

If both clients have messages ready, their handlers run sequentially. Every `MOVE` is checked against the current authoritative turn at dispatch time, including additional messages already buffered on a socket. The server does not pre-approve an entire batch based on the first message's turn. This prevents two handlers from both modifying the board using stale turn information.

No mutex acquisition/release pattern is needed because exactly one thread owns these resources. This safety depends on keeping handlers synchronous and avoiding worker threads or `await` inside game-state mutations. If a future design introduces another thread, the isolation assumption must be revisited.

## 5. Move validation and board synchronization

1. Parse and validate the message schema. Confirm the connection is registered and the game is in `PLAYER_TURN`; otherwise return `INVALID_SCHEMA` or `NOT_READY` as appropriate.
2. Derive player identity from the socket registration. Reject an inactive player's move with `NOT_YOUR_TURN`.
3. Require integer coordinates in `0..9` and reject booleans. Invalid coordinates return `INVALID_COORDINATE`. A previously attacked cell returns `ALREADY_ATTACKED`.
4. On rejection, queue `ERROR` only to the sender. Do not change the board, turn, attack history, or score.
5. On acceptance, record the attack, mark a hit or miss, detect a sunk ship, and check whether all 17 opposing ship cells have been hit. Hits and misses both consume one turn.
6. If play continues, switch to the other player. If the fleet is sunk, set the winner, finalize scores, and set `active_player=null`; no further turn is granted.
7. Build each player's `STATE_UPDATE` from the same completed state and enqueue both messages before dispatching another command. A player sees their own ships and only revealed attacks on the opponent's grid.
8. On a fleet victory, enqueue `GAME_OVER` after the final `STATE_UPDATE` on both connections. Both messages contain the finalized scores. On a disconnect, enqueue `GAME_OVER` with final state and scores to any reachable client.

Here, **synchronized broadcast means consistent state and ordering**, not guaranteed identical arrival times. Both clients are queued updates from the same committed state in one handler; TCP preserves message order on each connection. A client replaces its displayed board, turn, and scores from each complete server update. Network delay can make one display change later, and delivery cannot be guaranteed to a disconnected client. No application acknowledgement or delivery barrier is introduced in this sprint.

## 6. Round scores and leaderboard

A round is one complete CommandFleet match. Points measure match wins rather than hits or ships sunk:

| Outcome | Round points | Accumulated totals |
| --- | --- | --- |
| Opponent fleet sunk | Winner: 1; loser: 0 | Add one win and one point to the winner; add one loss to the loser. |
| Mid-game disconnect/forfeit | Remaining player: 1; departing player: 0 | Same scoring as a win/loss, recorded once when the failure is detected. The result remains identified as a forfeit. |
| Departure before setup finishes | Registered players: 0 | No wins, losses, or points added. |

Store current-round points by player ID in `round_scores`. Store cumulative `{wins, losses, points}` records by alias in `leaderboard`. A returning alias reuses its totals, while `Player_1` and `Player_2` are reassigned each round. Aliases are display identities in this classroom game, not authenticated accounts. The leaderboard sorts by points descending, then alias alphabetically for equal points. Totals live only in memory; no database or disk persistence is required.

Initialize a new alias with zero totals when it successfully joins. At match completion, use a single finalization function guarded by `result_recorded`. That function sets the result, awards points, updates totals, marks the result recorded, and creates score snapshots before any final messages are queued. A later EOF or failed final send must not award another win, change a fleet victory to a forfeit, or count another loss. If both players fail, the first processed departure ends the match; later failures cannot revise the recorded outcome.

The `scores` object defined in the updated protocol contains `round_scores` and `leaderboard`. Include it in every `STATE_UPDATE` and `GAME_OVER`. This sends initial totals at startup, the same unchanged table during ordinary turns, and the updated table at match completion. Both clients receive identical score data, even though their private board views differ. Cleanup clears round points but retains the leaderboard for later rounds.

## 7. Failure handling and implementation checks

- EOF, `DISCONNECT`, reset, or unrecoverable read/write error removes the failed connection. Before setup completes, use `abandoned`; during play, use `forfeit`. A link silently dropping does not necessarily produce an immediate EOF or RST; transport failure is handled when detected. Any configured liveness timeout must be checked by the event loop, not by blocking a socket read.
- A write failure while sending final messages does not change the result or scores. Each teardown checks whether the connection is still registered, preventing repeated unregister/close attempts when several ready events refer to a socket that has already closed.
- During `GAME_OVER` and `CLEANUP`, do not accept new game moves. Final-message draining continues through write events, then the room resets with no stale players or attacks.
- Implementation checks will cover two connected clients; out-of-turn and repeated attacks; fragmented/coalesced messages; partial sends; an unresponsive receiver; lobby and mid-game departures; duplicate failure notifications; and a second match without restarting the server. Verify that both clients display matching turn/score snapshots and that unsunk opponent ships remain hidden. These are planned checks, not a claim that game code has already been implemented or tested.

## References

- [Python 3.12 selectors documentation](https://docs.python.org/3.12/library/selectors.html): readiness events, registration, and removing sockets before close.
- [Python 3.12 socket documentation](https://docs.python.org/3.12/library/socket.html): non-blocking sockets and partial sends.
