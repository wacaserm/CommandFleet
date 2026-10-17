# CommandFleet application protocol blueprint

**Course:** CS 457 Computer Networks  
**Author:** Madison Wacaser  
**Version:** Sprint 1 protocol with Sprint 2 score and non-blocking I/O additions, 2026-10-17

## Game and connection model

CommandFleet has one game room and exactly two TCP clients. The server owns all ship locations, attacks, turns, and results. It randomly places each player's carrier (5 cells), battleship (4), cruiser (3), submarine (3), and destroyer (2) on separate 10 × 10 grids, without overlap or out-of-bounds cells. Ships may be horizontal or vertical. The server randomly chooses the first turn after both players join. Coordinates are zero-indexed integers `0..9`; each valid, previously unattacked coordinate consumes one turn. The first player to hit all 17 ship cells of the opposing fleet wins. There is no ordinary draw.

The deployment name is `server.wacaser.edu`. The TCP listening port is **45700**. Each client resolves the name to `192.168.20.100` in the planned CML topology. One server process hosts **one match at a time**. A third connection while a match or lobby is full receives `ERROR` with code `ROOM_FULL` and is closed. A second connection with an already used alias receives `ALIAS_TAKEN` and is closed. After `GAME_OVER`, both match clients are closed and the server resets its room; new clients can connect for another match on the same listening port. There are no rematches on the same client sockets.

## Transport and framing

- TCP carries UTF-8 JSON objects. Each complete compact JSON object is followed by exactly one LF byte (`0x0A`). A raw newline is never included inside a JSON string; JSON string escapes such as `\n` are allowed.
- A receiver maintains a **byte** buffer across `recv()` calls, extracts every complete line through LF, then decodes UTF-8 and parses each line as one JSON object. It must handle multiple lines in one read and a line split across reads. An empty line is a malformed message. A line may contain at most **8192 bytes before LF**; exceeding this limit causes an `ERROR` (`MESSAGE_TOO_LARGE`) and connection closure.
- The sender serializes one object with no pretty-printing and appends `b"\n"`. A blocking client can use `sendall()`. The Sprint 2 non-blocking server uses an output queue and `send()` on write-ready events, retaining unsent bytes after partial writes or `BlockingIOError`. JSON numbers are parsed into integers where integer fields are required; booleans, despite Python's `bool` subclassing `int`, are rejected for coordinate fields.
- The protocol is one JSON object per line, independent of TCP segment boundaries. For example, the following is **one continuous byte stream** containing two messages, with `\n` representing the single LF byte:

```text
{"msg_type":"CONNECT","payload":{"alias":"Madison"}}\n{"msg_type":"MOVE","payload":{"row":0,"col":2}}\n
```

If the first `recv()` contains only `{"msg_type":"CON`, the receiver waits for the remaining bytes; it does not attempt to parse a partial object. If a read contains both complete lines, it processes both in order.

## Common structure and validation

Every message is an object with **exactly** `msg_type` (string) and `payload` (object). All listed payload fields are required; unexpected fields are rejected with `INVALID_SCHEMA`. There is no client-supplied `player_id`: the server associates each socket with the player ID assigned at `CONNECT`, preventing a client from claiming the other player's turn. A player alias is for display only. `player_id` values are the server-assigned strings `Player_1` and `Player_2`. `null` is allowed only where explicitly specified. No timestamps are required; arrival order on each TCP connection controls processing.

| Message | Direction | Required payload fields and types | Meaning |
| --- | --- | --- | --- |
| `CONNECT` | Client → server | `alias`: string of 1–20 ASCII letters, digits, `_` or `-` | Join the room. Each socket may send this once, before other commands. |
| `LOBBY_WAIT` | Server → client | `player_id`: string (`Player_1`); `message`: string | Acknowledges the first player and waits for a second connection. |
| `GAME_START` | Server → each client | `player_id`: string (recipient's ID); `opponent_alias`: string; `active_player`: string | Starts match and assigns roles. Each player then receives its own `STATE_UPDATE`. |
| `MOVE` | Client → server | `row`: integer `0..9`; `col`: integer `0..9` | Attack one cell of the opponent's grid. |
| `STATE_UPDATE` | Server → each client | `active_player`: player ID string, or `null` when the match has ended; `your_board`: board object; `opponent_board`: board object; `last_move`: move-result object or `null`; `your_ships_remaining`: integer `0..5`; `opponent_ships_remaining`: integer `0..5`; `scores`: score object | Sends recipient-specific view after game start and each accepted move, including the winning move. The final update has no next active player and is followed by `GAME_OVER`. |
| `ERROR` | Server → client | `code`: string from table below; `message`: string | Explains rejected command or invalid message. A rejected move does not consume the turn. |
| `DISCONNECT` | Client → server | `reason`: string (`quit`) | Intentional departure. Server then sends the other player `GAME_OVER` if the game has begun. |
| `GAME_OVER` | Server → each available client | `result`: string (`fleet_sunk`, `forfeit`, or `abandoned`); `winner`: player ID string or `null`; `reason`: string; `final_state`: object with exactly `your_board`, `opponent_board`, `your_ships_remaining`, and `opponent_ships_remaining` using the types above, or `null` for `abandoned`; `scores`: score object | Ends the match. `fleet_sunk` is a completed win; `forfeit` identifies the remaining player as winner by forfeit; `abandoned` means a player left before setup was committed, and `winner` is `null`. Scores are finalized once before this message is queued. |

A **board object** has `size` (integer `10`) and `cells` (array of exactly 10 strings of length 10). For `your_board`, a cell is `.` (empty, unshot), `S` (own intact ship), `o` (opponent miss), or `X` (own ship hit). For `opponent_board`, a cell is `?` (untried, ship locations hidden), `o` (own miss), or `X` (own hit). In the final state, the same visibility rule applies: unhit opponent ship locations stay hidden. `last_move`, when present, has `by` (player ID string), `row` and `col` (integers), `outcome` (`hit` or `miss`), and `sunk_ship` (fleet-name string or `null`). It reports the accepted attack to both clients without revealing other ship locations.

| Error code | Condition | Connection action |
| --- | --- | --- |
| `INVALID_SCHEMA` | Bad JSON/UTF-8, wrong shape, wrong non-coordinate field type, unsupported `msg_type`, repeated `CONNECT`, or invalid alias | Send error; keep open unless recovery is impossible. |
| `MESSAGE_TOO_LARGE` | Incoming line exceeds 8192 bytes | Send error; close this socket. |
| `ROOM_FULL` / `ALIAS_TAKEN` | Connection cannot join | Send error; close new socket. |
| `NOT_READY` | `MOVE` before both clients joined | Send error; keep turn/state. |
| `NOT_YOUR_TURN` | Inactive player sends `MOVE` | Send error; keep turn/state. |
| `INVALID_COORDINATE` | Row/col not an integer in `0..9` | Send error; keep turn/state. |
| `ALREADY_ATTACKED` | Cell previously targeted by this player | Send error; keep turn/state. |

## Score object (Sprint 2)

The `scores` object has exactly two fields:

| Field | Type and structure |
| --- | --- |
| `round_scores` | Array of objects with exactly `player_id` (assigned ID string), `alias` (validated alias string), and `points` (integer `0` or `1`). It contains the currently registered players, so a running match has two records; an abandoned lobby can have fewer. Sort by player ID. |
| `leaderboard` | Array of objects with exactly `alias` (validated alias string), `wins` (nonnegative integer), `losses` (nonnegative integer), and `points` (nonnegative integer equal to wins). Sort by points descending, then alias alphabetically. |

Each match starts with round points of zero. A fleet victory or mid-game forfeit gives the winner one point and one win, and the loser one loss. An abandoned setup awards no points, wins, or losses. The leaderboard starts a new alias at zero and accumulates totals across matches for this server process. Clearing a match does not clear the leaderboard. A process restart clears it. Both clients receive the same score object, including the updated table at round completion. See `concurrency_design.md` for the server-memory structures and single-update guard.

## Representative messages

Each line below is a separate JSON message; the sender appends LF to each.

```json
{"msg_type":"CONNECT","payload":{"alias":"Madison"}}
{"msg_type":"LOBBY_WAIT","payload":{"player_id":"Player_1","message":"Waiting for Player_2"}}
{"msg_type":"GAME_START","payload":{"player_id":"Player_1","opponent_alias":"Alex","active_player":"Player_2"}}
{"msg_type":"MOVE","payload":{"row":0,"col":2}}
{"msg_type":"ERROR","payload":{"code":"ALREADY_ATTACKED","message":"Cell (0,2) was already attacked"}}
{"msg_type":"DISCONNECT","payload":{"reason":"quit"}}
```

The compact example below shows the state after a Player 1 hit at (0,2). The sample board rows show a possible layout only; clients never receive an opponent's intact ship cells.

```json
{"msg_type":"STATE_UPDATE","payload":{"active_player":"Player_2","your_board":{"size":10,"cells":["SSSSS.....","SSSS......","SSS.......","SSS.......","SS........","..........","..........","..........","..........",".........."]},"opponent_board":{"size":10,"cells":["??X???????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????"]},"last_move":{"by":"Player_1","row":0,"col":2,"outcome":"hit","sunk_ship":null},"your_ships_remaining":5,"opponent_ships_remaining":5,"scores":{"round_scores":[{"player_id":"Player_1","alias":"Madison","points":0},{"player_id":"Player_2","alias":"Alex","points":0}],"leaderboard":[{"alias":"Alex","wins":0,"losses":0,"points":0},{"alias":"Madison","wins":0,"losses":0,"points":0}]}}}
{"msg_type":"GAME_OVER","payload":{"result":"forfeit","winner":"Player_1","reason":"Player_2 disconnected","final_state":{"your_board":{"size":10,"cells":["SSSSS.....","SSSS......","SSS.......","SSS.......","SS........","..........","..........","..........","..........",".........."]},"opponent_board":{"size":10,"cells":["??X???????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????"]},"your_ships_remaining":5,"opponent_ships_remaining":5},"scores":{"round_scores":[{"player_id":"Player_1","alias":"Madison","points":1},{"player_id":"Player_2","alias":"Alex","points":0}],"leaderboard":[{"alias":"Madison","wins":1,"losses":0,"points":1},{"alias":"Alex","wins":0,"losses":1,"points":0}]}}}
```

## Connection lifecycle

An intentional departure sends `DISCONNECT` before closing. If it happens during a match, the opponent receives `GAME_OVER` with `result="forfeit"`; the fleet was **not** sunk. If it happens in the lobby, the room ends as `abandoned`, with no winner and `final_state=null`. A connection can also end without that message: `recv()` returning `b""` means TCP EOF (clean close), and `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`, or a detected transport timeout means the connection failed. Treat these as the same state transition as a disconnect; do not spin on EOF. An unreachable opponent cannot be guaranteed to receive `GAME_OVER`. On any terminal transition, finalize scores once and queue notifications. Drain final output using write-ready events, with a finite close deadline, before unregistering and closing match sockets. Clear player IDs, boards, attacks, round scores, and turn for the next match, but retain the leaderboard. **Keep the listening socket open** while the server is running; close it only when the server process shuts down. All sends can fail and should be caught during cleanup. A disconnected client cannot reconnect to a match already in progress; it may join a later match after the room resets.
