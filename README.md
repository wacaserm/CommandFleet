# CommandFleet

A two-player naval strategy game for CS 457 Computer Networks. The server
places five ships per player, tracks attacks and turns, and keeps the opponent's
unhit ships hidden. Sprint 3 runs on localhost using Python 3.10+ and the
standard library.

## Play locally

Run these commands from the repository root in three separate terminals,
starting with the server. On Windows, `py -3` can replace `python3`.

| Terminal | Command |
| --- | --- |
| Server | `python3 server.py -p 45700` |
| First client | `python3 client.py -i localhost -p 45700 --alias Madison` |
| Second client | `python3 client.py -i localhost -p 45700 --alias Alex` |

When the client says **Your Turn**, enter a row and column from 0 through 9,
such as `3 7`. Both hits and misses use a turn. The game ends when all 17
opposing ship cells have been hit. Type `quit` to leave; leaving during a
match gives the other player a forfeit win. To start another match, launch
both clients again while the server keeps running. Ctrl+C stops the server.

Use `python3 server.py -h` and `python3 client.py -h` for help. The
`--alias` flag is optional; omit it to get an interactive prompt.
The client resolves whatever name is passed to `-i`. For Sprint 4, use
`-i server.wacaser.edu` once that record is configured in CML.

## Verify Sprint 3

- [Gameplay log](gameplay_log.txt) contains real output from one server and
  two CLI clients playing four accepted turns. It also shows the forfeit
  result and the room reopening.
- Local socket checks covered split and combined frames, oversized and
  malformed messages, rejected moves, hidden fleets, a 33-turn fleet
  victory, an EOF forfeit, and leaderboard totals surviving a room reset.
- To exercise DNS failure, run
  `python3 client.py -i commandfleet-test.invalid -p 45700`. The client
  prints Cisco DNS configuration guidance and exits with status 1.

## Design documents

- [Protocol blueprint](protocol_blueprint.md) and
  [server state machine](fsm_specification.md)
- [Concurrency design](concurrency_design.md) and
  [sequence diagrams](sequence_diagram.md)
- [AI implementation plan](ai_prompts.md) and
  [statement of work](sow_template.md)

The leaderboard lives in memory. It retains up to 64 unique aliases per
server process to keep score snapshots within the 8,192-byte frame limit.

## Sprint 3 gameplay evidence

I tested CommandFleet on localhost with one server and two clients in separate
PowerShell terminals. Both clients connected, and the server recorded at least
three accepted turns as play alternated between them.

- [Gameplay screenshot](evidence\Screenshot 2026-11-13 003229.png)
- [Console log](gameplay_log.txt)
- [DNS failure screenshot](evidence/Screenshot 2026-11-13 003345.png): The client
  printed Cisco DNS troubleshooting guidance and exited with code `1`
  without a traceback.