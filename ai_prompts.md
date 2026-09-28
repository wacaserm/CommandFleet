# CommandFleet AI prompting and constraint strategy

**Author:** Madison Wacaser  
**Status:** Sprint 1 implementation plan. Prompts below are planned templates; they are not presented as a record of prompts already used.

I will use ChatGPT as a coding assistant for the Python server and clients. I will supply `protocol_blueprint.md` and `fsm_specification.md` with each coding request and treat the checked-in documents as the source of truth. I will request small, reviewable units in this order: (1) JSON framing and validation, (2) pure Battleship rules, (3) server state transitions and socket lifecycle, (4) client input/display, (5) tests and CML deployment fixes. I will review generated code and run local checks before integrating each unit; I will not accept a changed field name, message type, board visibility rule, or win/forfeit/reset interpretation without first deliberately updating the design documents.

## Prompt 1: parser and serializer

```text
Read the attached protocol_blueprint.md and fsm_specification.md as binding specifications. Write Python 3 functions encode_message(message) -> bytes and receive_messages(sock, buffer) -> (messages, remaining_buffer) for newline-delimited UTF-8 JSON. The wire format is exactly one compact JSON object followed by LF per message. The receiver must buffer partial reads, parse several messages from one recv, enforce an 8192-byte maximum before LF, reject invalid UTF-8 and JSON, and treat b"" as EOF. Validate that top-level keys are exactly msg_type and payload, and reject missing/extra fields and wrong types for each message. In particular, reject boolean coordinates. Do not add a player_id or timestamp supplied by clients. Use the exact message names, fields, and values in the blueprint. Show the functions and a concise explanation of errors; do not add new protocol messages.
```

## Prompt 2: game rules and privacy

```text
Implement a pure Python CommandFleet game model from the attached specifications. Make two independent 10x10 boards with lengths 5, 4, 3, 3, 2, random nonoverlapping horizontal/vertical placement, zero-indexed row/col, alternating accepted attacks, repeated-shot rejection without losing a turn, ship-sunk reporting, and a win when all 17 opposing ship cells are hit. Generate separate STATE_UPDATE views for each recipient. The opponent_board must never expose an unhit opponent ship, including in GAME_OVER. Keep the game model free of socket code. Write focused tests for placement, invalid/repeated moves, turn retention, sunk ships, victory, and hidden information.
```

## Prompt 3: server FSM and disconnects

```text
Implement the server's one-room-at-a-time, two-player FSM exactly as fsm_specification.md describes. Bind TCP port 45700. Model GAME_START as an explicit setup state. Associate player identity with the accepted socket after CONNECT; never trust a client-supplied identity. Send each player recipient-specific updates. Handle both connections so an out-of-turn MOVE receives NOT_YOUR_TURN without blocking the active player. Catch recv EOF, reset, broken pipe, and configured timeouts; handle DISCONNECT intentionally. Before game start use abandoned with winner null and final_state null; after game start use forfeit for the remaining player. Make GAME_OVER terminal for the current match. Close both match sockets and clear every match-specific field in CLEANUP, even if notification fails, then return to WAITING_FOR_PLAYERS while retaining the listening socket. Subsequent matches use new client sockets. State your concurrency choice and how game state stays consistent; do not change the protocol or invent in-progress reconnection.
```

## Prompt 4: client and review

```text
Build a command-line client for server.wacaser.edu:45700 using only the messages in protocol_blueprint.md. It sends CONNECT once, displays LOBBY_WAIT, GAME_START, its own STATE_UPDATE, ERROR, and GAME_OVER, and accepts row/col input only for MOVE. On user quit, send DISCONNECT with reason quit before closing if possible. Use the specified newline framing and handle partial/multiple messages per recv. Then review client/server code against the two design documents and list any discrepancies rather than silently changing the design.
```

## Verification and time plan

- Test framing with one message split over several socket reads and several messages in one read. Test malformed JSON, invalid UTF-8, missing/extra fields, oversized lines, wrong coordinate types, and EOF; verify no busy loop on `b""`.
- Test two local clients through a complete game. Confirm invalid and out-of-turn attacks preserve the active turn, each player sees only permitted board information, and the final successful attack produces exactly one `fleet_sunk` terminal result.
- Test orderly quit and a killed client process during both lobby and game states. Check the difference between `abandoned` and `forfeit`, error handling on sends, and resource cleanup. Then connect a fresh pair without restarting the server, confirm both are assigned new `Player_1`/`Player_2` roles, start with clean boards and attack histories, and complete a second match.
- Keep protocol code separate from game rules so networking bugs are easier to isolate. Prioritize a working two-client local game before moving the same Python files to the three CML nodes; then verify routing, DNS, and packet captures. Review AI output line by line against the protocol documents and retain working checkpoints in Git as each stage passes.
