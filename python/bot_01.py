#!/usr/bin/env python3
"""
Bot 01 - Sushi Go bot for team fishermen.

Usage:
    python bot_01.py <game_id> <player_name> [host] [port]
    python bot_01.py <host> <port> <game_id> <player_name>

Example:
    python bot_01.py abc123 Bot01
    python bot_01.py abc123 Bot01 192.168.1.50 7878
"""

import json
import re
import socket
import sys
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
#  GAME STATE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class GameState:
    """Everything we know about the current game."""

    game_id: str
    player_id: int
    rejoin_token: str

    # Current hand (updated each turn when HAND is received)
    hand: list[str] = field(default_factory=list)

    # Cards we have played in front of us this round (our tableau)
    my_tableau: list[str] = field(default_factory=list)

    # The most recently played card (updated each turn before we send PLAY)
    last_card_played: Optional[str] = None

    # What every player revealed last turn: {player_name: [card, ...]}
    last_turn_reveals: dict[str, list[str]] = field(default_factory=dict)

    # Round (1-3) and turn within the round
    round: int = 1
    turn: int = 1

    # ── Convenience helpers ──────────────────────────────────────────────────

    def count(self, card: str) -> int:
        """How many of a card type are in my tableau this round."""
        return self.my_tableau.count(card)

    @property
    def has_chopsticks(self) -> bool:
        """True if Chopsticks are available in our tableau."""
        return "Chopsticks" in self.my_tableau

    @property
    def has_unused_wasabi(self) -> bool:
        """True if we have a Wasabi card not yet paired with a Nigiri."""
        wasabi = self.count("Wasabi")
        nigiri = sum(self.count(c) for c in ("Egg Nigiri", "Salmon Nigiri", "Squid Nigiri"))
        return wasabi > nigiri

    @property
    def puddings(self) -> int:
        """Total puddings collected this round."""
        return self.count("Pudding")


# ══════════════════════════════════════════════════════════════════════════════
#  ALGORITHM  ← fill in your logic here
# ══════════════════════════════════════════════════════════════════════════════

def choose_card(hand: list[str], state: GameState) -> int:
    """
    Decide which card to play this turn.

    Args:
        hand:   Cards currently in hand, e.g. ["Tempura", "Salmon Nigiri", "Pudding"]
        state:  Full game state — see GameState above.

    Returns:
        0-based index into `hand` of the card to play.

    Useful state attributes:
        state.my_tableau          — cards you've already played this round
        state.last_card_played    — card you played last turn (None on first turn)
        state.last_turn_reveals   — {player_name: [cards]} everyone showed last turn
        state.has_unused_wasabi   — True if you have a Wasabi without a Nigiri
        state.has_chopsticks      — True if Chopsticks are in your tableau
        state.round               — current round number (1, 2, or 3)
        state.turn                — turn number within the current round
        state.count("Sashimi")    — how many of a card you have in your tableau
        state.puddings            — pudding count this round
    """
    # ──────────────────────────────────────────────────────────────────────────
    #  INSERT ALGORITHM HERE
    # ──────────────────────────────────────────────────────────────────────────

    # Placeholder: play first card
    return 0


def choose_chopsticks(hand: list[str], state: GameState) -> Optional[tuple[int, int]]:
    """
    (Optional) Decide whether to use Chopsticks this turn.

    Only called when state.has_chopsticks is True.
    Return (idx1, idx2) to play two cards at once, or None to play a single card.

    Args:
        hand:   Cards currently in hand
        state:  Full game state

    Returns:
        (idx1, idx2) tuple to use chopsticks, or None to skip and call choose_card instead.
    """
    # ──────────────────────────────────────────────────────────────────────────
    #  INSERT CHOPSTICKS LOGIC HERE  (or leave None to never use them)
    # ──────────────────────────────────────────────────────────────────────────
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  NETWORKING / PROTOCOL  (no need to touch this section)
# ══════════════════════════════════════════════════════════════════════════════

class SushiGoBot:

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.sock_file = None
        self.state: Optional[GameState] = None

    # ── Low-level I/O ────────────────────────────────────────────────────────

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.sock_file = self.sock.makefile("r", encoding="utf-8", errors="replace")
        print(f"Connected to {self.host}:{self.port}")

    def disconnect(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, cmd: str):
        print(f">>> {cmd}")
        self.sock.sendall((cmd + "\n").encode("utf-8"))

    def recv(self) -> str:
        line = self.sock_file.readline()
        if line == "":
            raise ConnectionError("Server closed connection")
        msg = line.strip()
        print(f"<<< {msg}")
        return msg

    def recv_until(self, predicate) -> str:
        while True:
            msg = self.recv()
            if msg and predicate(msg):
                return msg

    # ── Protocol helpers ─────────────────────────────────────────────────────

    def join_game(self, game_id: str, player_name: str) -> bool:
        """Send JOIN, wait for WELCOME or ERROR."""
        self.send(f"JOIN {game_id} {player_name}")
        response = self.recv_until(
            lambda m: m.startswith("WELCOME") or m.startswith("ERROR")
        )
        if response.startswith("WELCOME"):
            # WELCOME <game_id> <player_id> <rejoin_token>
            parts = response.split()
            self.state = GameState(
                game_id=parts[1],
                player_id=int(parts[2]),
                rejoin_token=parts[3] if len(parts) > 3 else "",
            )
            print(f"Joined game '{self.state.game_id}' as player {self.state.player_id}")
            print(f"Rejoin token: {self.state.rejoin_token}")
            return True
        print(f"Failed to join: {response}")
        return False

    def parse_hand(self, message: str) -> list[str]:
        """Parse 'HAND 0:Tempura 1:Salmon Nigiri ...' into a card-name list."""
        payload = message[len("HAND "):].strip()
        cards = []
        for match in re.finditer(r"(\d+):(.*?)(?=\s+\d+:|$)", payload):
            cards.append(match.group(2).strip())
        return cards

    def parse_played(self, message: str) -> dict[str, list[str]]:
        """Parse 'PLAYED Alice:Squid Nigiri; Bob:Tempura' into {name: [cards]}."""
        reveals: dict[str, list[str]] = {}
        payload = message[len("PLAYED "):].strip()
        for entry in payload.split(";"):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            name, cards_str = entry.split(":", 1)
            reveals[name.strip()] = [c.strip() for c in cards_str.split(",") if c.strip()]
        return reveals

    # ── Turn execution ───────────────────────────────────────────────────────

    def play_turn(self):
        """Ask the algorithm for a move and send it to the server."""
        hand = self.state.hand
        if not hand:
            return

        # Try chopsticks first if available
        if self.state.has_chopsticks:
            chop = choose_chopsticks(hand, self.state)
            if chop is not None:
                idx1, idx2 = chop
                self._record_play(hand[idx1])
                self.state.my_tableau.append(hand[idx2])
                # Chopsticks card returns to hand (remove it from tableau)
                if "Chopsticks" in self.state.my_tableau:
                    self.state.my_tableau.remove("Chopsticks")
                self.send(f"CHOPSTICKS {idx1} {idx2}")
                return

        card_index = choose_card(hand, self.state)
        self._record_play(hand[card_index])
        self.send(f"PLAY {card_index}")

    def _record_play(self, card: str):
        """Update state to reflect that we just played a card."""
        self.state.last_card_played = card
        self.state.my_tableau.append(card)

    # ── Server message handlers ──────────────────────────────────────────────

    def handle_round_start(self, message: str):
        parts = message.split()
        self.state.round = int(parts[1])
        self.state.turn = 1
        self.state.my_tableau = []
        self.state.last_card_played = None
        self.state.last_turn_reveals = {}
        print(f"\n{'─'*40}")
        print(f"  ROUND {self.state.round} STARTING")
        print(f"{'─'*40}")

    def handle_played(self, message: str):
        """Cards were revealed — record what everyone played and advance turn."""
        self.state.last_turn_reveals = self.parse_played(message)
        self.state.turn += 1

    def handle_round_end(self, message: str):
        # ROUND_END <round> <scores_json>
        parts = message.split(None, 2)
        if len(parts) >= 3:
            try:
                scores = json.loads(parts[2])
                print(f"\nRound {parts[1]} scores: {scores}")
            except (json.JSONDecodeError, IndexError):
                pass

    def handle_game_end(self, message: str):
        # GAME_END <final_scores_json> <winners_json>
        parts = message.split(None, 2)
        try:
            scores = json.loads(parts[1]) if len(parts) > 1 else {}
            winners = json.loads(parts[2]) if len(parts) > 2 else []
            print(f"\nFinal scores: {scores}")
            print(f"Winner(s): {winners}")
        except (json.JSONDecodeError, IndexError):
            pass
        print("Game over!")

    # ── Main game loop ───────────────────────────────────────────────────────

    def run(self, game_id: str, player_name: str):
        try:
            self.connect()

            if not self.join_game(game_id, player_name):
                return

            self.send("READY")

            while True:
                msg = self.recv()
                if not msg:
                    continue

                if msg.startswith("GAME_END"):
                    self.handle_game_end(msg)
                    break
                elif msg.startswith("ROUND_START"):
                    self.handle_round_start(msg)
                elif msg.startswith("PLAYED"):
                    self.handle_played(msg)
                elif msg.startswith("ROUND_END"):
                    self.handle_round_end(msg)
                elif msg.startswith("HAND"):
                    self.state.hand = self.parse_hand(msg)
                    self.play_turn()
                # OK, WAITING, JOINED, GAME_START — logged automatically, no action needed

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            self.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 3:
        print("Usage: python bot_01.py <game_id> <player_name> [host] [port]")
        print("   or: python bot_01.py <host> <port> <game_id> <player_name>")
        sys.exit(1)

    args = sys.argv[1:]
    host = "localhost"
    port = 7878

    # Support both argument orders (match first_card_bot.py convention)
    if len(args) >= 4 and args[1].isdigit():
        host, port = args[0], int(args[1])
        game_id, player_name = args[2], args[3]
    else:
        game_id, player_name = args[0], args[1]
        if len(args) > 2:
            host = args[2]
        if len(args) > 3:
            port = int(args[3])

    SushiGoBot(host, port).run(game_id, player_name)


if __name__ == "__main__":
    main()
