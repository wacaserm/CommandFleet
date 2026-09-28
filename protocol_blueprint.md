# CommandFleet application protocol blueprint

**Course:** CS 457 Computer Networks  
**Author:** Madison Wacaser  
**Version:** Sprint 1 design, 2026-09-27

## Game and connection model

CommandFleet has one game room and exactly two TCP clients. The server owns all ship locations, attacks, turns, and results. It randomly places each player's carrier (5 cells), battleship (4), cruiser (3), submarine (3), and destroyer (2) on separate 10 × 10 grids, without overlap or out-of-bounds cells. Ships may be horizontal or vertical. The server randomly chooses the first turn after both players join. Coordinates are zero-indexed integers `0..9`; each valid, previously unattacked coordinate consumes one turn. The first player to hit all 17 ship cells of the opposing fleet wins. There is no ordinary draw.

The deployment name is `server.wacaser.edu`. The TCP listening port is **45700**. Each client resolves the name to `192.168.20.100` in the planned CML topology. One server process hosts **one match at a time**. A third connection while a match or lobby is full receives `ERROR` with code `ROOM_FULL` and is closed. A second connection with an already used alias receives `ALIAS_TAKEN` and is closed. After `GAME_OVER`, both match clients are closed and the server resets its room; new clients can connect for another match on the same listening port. There are no rematches on the same client sockets.

## Transport and framing

- TCP carries UTF-8 JSON objects. Each complete compact JSON object is followed by exactly one LF byte (`0x0A`). A raw newline is never included inside a JSON string; JSON string escapes such as `\n` are allowed.
- A receiver maintains a **byte** buffer across `recv()` calls, extracts every complete line through LF, then decodes UTF-8 and parses each line as one JSON object. It must handle multiple lines in one read and a line split across reads. An empty line is a malformed message. A line may contain at most **8192 bytes before LF**; exceeding this limit causes an `ERROR` (`MESSAGE_TOO_LARGE`) and connection closure.
- The sender serializes one object with no pretty-printing, appends `b"\n"`, and calls `sendall()`. JSON numbers are parsed into integers where integer fields are required; booleans, despite Python's `bool` subclassing `int`, are rejected for coordinate fields.
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
| `STATE_UPDATE` | Server → each client | `active_player`: string; `your_board`: board object; `opponent_board`: board object; `last_move`: move-result object or `null`; `your_ships_remaining`: integer `0..5`; `opponent_ships_remaining`: integer `0..5` | Sends recipient-specific view after game start and each accepted move. |
| `ERROR` | Server → client | `code`: string from table below; `message`: string | Explains rejected command or invalid message. A rejected move does not consume the turn. |
| `DISCONNECT` | Client → server | `reason`: string (`quit`) | Intentional departure. Server then sends the other player `GAME_OVER` if the game has begun. |
| `GAME_OVER` | Server → each available client | `result`: string (`fleet_sunk`, `forfeit`, or `abandoned`); `winner`: player ID string or `null`; `reason`: string; `final_state`: same recipient-specific board/score fields as `STATE_UPDATE` except `active_player` and `last_move`, or `null` for `abandoned` | Ends the match. `fleet_sunk` is a completed win; `forfeit` identifies the remaining player as winner by forfeit; `abandoned` means a player left before play began, when no boards exist, and `winner` is `null`. |

A **board object** has `size` (integer `10`) and `cells` (array of exactly 10 strings of length 10). For `your_board`, a cell is `.` (empty, unshot), `S` (own intact ship), `o` (opponent miss), or `X` (own ship hit). For `opponent_board`, a cell is `?` (untried, ship locations hidden), `o` (own miss), or `X` (own hit). In the final state, the same visibility rule applies: unhit opponent ship locations stay hidden. `last_move`, when present, has `by` (player ID string), `row` and `col` (integers), `outcome` (`hit` or `miss`), and `sunk_ship` (fleet-name string or `null`). It reports the accepted attack to both clients without revealing other ship locations.

| Error code | Condition | Connection action |
| --- | --- | --- |
| `INVALID_SCHEMA` | Bad JSON/UTF-8, wrong shape/type, unsupported `msg_type`, or invalid alias | Send error; keep open unless recovery is impossible. |
| `MESSAGE_TOO_LARGE` | Incoming line exceeds 8192 bytes | Send error; close this socket. |
| `ROOM_FULL` / `ALIAS_TAKEN` | Connection cannot join | Send error; close new socket. |
| `NOT_READY` | `MOVE` before both clients joined | Send error; keep turn/state. |
| `NOT_YOUR_TURN` | Inactive player sends `MOVE` | Send error; keep turn/state. |
| `INVALID_COORDINATE` | Row/col not an integer in `0..9` | Send error; keep turn/state. |
| `ALREADY_ATTACKED` | Cell previously targeted by this player | Send error; keep turn/state. |

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
{"msg_type":"STATE_UPDATE","payload":{"active_player":"Player_2","your_board":{"size":10,"cells":["S.........","S.........","S.........","S.........","S.........","..........","..........","..........","..........",".........."]},"opponent_board":{"size":10,"cells":["??X???????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????"]},"last_move":{"by":"Player_1","row":0,"col":2,"outcome":"hit","sunk_ship":null},"your_ships_remaining":5,"opponent_ships_remaining":5}}
{"msg_type":"GAME_OVER","payload":{"result":"forfeit","winner":"Player_1","reason":"Player_2 disconnected","final_state":{"your_board":{"size":10,"cells":["S.........","S.........","S.........","S.........","S.........","..........","..........","..........","..........",".........."]},"opponent_board":{"size":10,"cells":["??X???????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????","??????????"]},"your_ships_remaining":5,"opponent_ships_remaining":5}}}
```

## Connection lifecycle

An intentional departure sends `DISCONNECT` before closing. If it happens during a match, the opponent receives `GAME_OVER` with `result="forfeit"`; the fleet was **not** sunk. If it happens in the lobby, the room ends as `abandoned`, with no winner and `final_state=null`. A connection can also end without that message: `recv()` returning `b""` means TCP EOF (clean close), and `ConnectionResetError`, `ConnectionAbortedError`, `BrokenPipeError`, or a configured socket timeout means the transport failed. Treat these as the same state transition as a disconnect; do not spin on EOF. An unreachable opponent cannot be guaranteed to receive `GAME_OVER`. On any terminal transition, attempt to notify connected clients, close both match client sockets, and clear the player IDs, boards, attacks, and turn for the next match. **Keep the listening socket open** while the server is running; close it only when the server process shuts down. All sends can fail and should be caught during cleanup. A disconnected client cannot reconnect to a match already in progress; it may join a later match after the room resets.
