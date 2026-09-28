# CommandFleet

CommandFleet is an in-progress, two-player naval strategy game for the command line. I am designing it for my CS 457 Computer Networks project to practice TCP application protocols, server-managed game state, and deployment across a simulated network.

## Current status

**Sprint 1: protocol and server state machine designed.** The repository currently contains design documents and a minimal Python entry point. The playable server and client are planned work; the game cannot be played yet.

## Design

- Two players connect to one Python server over TCP. The server manages a single match at a time and can accept a new match after cleanup.
- Messages are UTF-8 JSON objects separated by newline bytes. The receiver must handle messages split across reads or combined in one read.
- The server owns fleet placement, turn order, move validation, and results. Each player receives a view that hides the opponent's unhit ships.
- The planned game uses two 10 × 10 boards and five randomly placed ships per player. A player wins by hitting all 17 cells of the opposing fleet.
- The state machine distinguishes completed wins, forfeits after play begins, and abandoned lobbies. Rejected moves do not consume a turn.

## Project documents

| Document | What it covers |
| --- | --- |
| [Protocol blueprint](protocol_blueprint.md) | TCP framing, message schemas, validation, recipient-specific board views, and disconnect handling |
| [Server state machine](fsm_specification.md) | State transitions, move handling, cleanup, and invariants |
| [AI implementation plan](ai_prompts.md) | Planned implementation prompts and verification steps |
| [Statement of work](sow_template.md) | Course scope and future sprint sections |

## Next steps

1. Implement and test JSON-line framing and message validation.
2. Implement fleet placement, attacks, turn tracking, and private board views.
3. Connect the server and two clients for a complete local game.
4. Deploy and inspect the game in the planned CML network topology.

**Stack:** Python, TCP sockets, JSON, Cisco Modeling Labs (planned deployment).
