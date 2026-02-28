#!/usr/bin/env python3
"""
Bot 01 - Sushi Go bot for team fishermen.

Usage:
    python bot_01.py <game_id> <player_name> [host] [port]
    python bot_01.py <host> <port> <game_id> <player_name>

Example:
    python bot_01.py abc123 Bot01
    python bot_01.py abc123 Bot01 192.168.1.50 7878

Run tests:
    python bot_01.py --test
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
    player_name: str

    # ── Current hand ─────────────────────────────────────────────────────────
    # Updated each turn when HAND is received
    hand: list[str] = field(default_factory=list)

    # ── Hand rotation tracking ───────────────────────────────────────────────
    # Number of players in this game (set from GAME_START)
    player_count: int = 0

    # All hands we've held this round, indexed by turn order.
    #   all_hands[0]                  = our starting hand this round
    #   all_hands[current_hand_ptr]   = hand we are currently holding
    #   all_hands[current_hand_ptr+1] = next hand we'll receive (None until seen)
    # Cards are removed from the relevant slot as we play them.
    # By the end of the round all slots will be populated.
    all_hands: list = field(default_factory=list)   # list[Optional[list[str]]]

    # Points at the slot in all_hands we are currently holding
    current_hand_ptr: int = 0

    # ── Tableau tracking ─────────────────────────────────────────────────────
    # Cards we have played in front of us this round
    my_tableau: list[str] = field(default_factory=list)

    # What every player has played this round: {player_name: [card, ...]}
    # Updated after each PLAYED message — use this to see opponents' boards
    all_tableaux: dict[str, list[str]] = field(default_factory=dict)

    # ── Turn-level info ───────────────────────────────────────────────────────
    # The card we played most recently (set just before PLAY is sent)
    last_card_played: Optional[str] = None

    # What every player revealed last turn: {player_name: [card, ...]}
    last_turn_reveals: dict[str, list[str]] = field(default_factory=dict)

    # Round (1-3) and turn within the round
    round: int = 1
    turn: int = 1

    # ── Convenience helpers ──────────────────────────────────────────────────

    @property
    def next_hand(self) -> Optional[list[str]]:
        """The hand we'll receive next turn (None if not yet seen this round)."""
        idx = self.current_hand_ptr + 1
        if idx < len(self.all_hands):
            return self.all_hands[idx]
        return None

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

    ── Card name strings (exact, case-sensitive) ────────────────────────────
        "Tempura"           — 5 pts per pair in your tableau
        "Sashimi"           — 10 pts per set of 3 in your tableau
        "Dumpling"          — 1/3/6/10/15 pts for 1/2/3/4/5+ dumplings
        "Maki Roll (1)"     — 1 maki symbol; most maki = 6 pts, 2nd most = 3 pts
        "Maki Roll (2)"     — 2 maki symbols
        "Maki Roll (3)"     — 3 maki symbols
        "Egg Nigiri"        — 1 pt (3 pts if played on a Wasabi)
        "Salmon Nigiri"     — 2 pts (6 pts if played on a Wasabi)
        "Squid Nigiri"      — 3 pts (9 pts if played on a Wasabi)
        "Wasabi"            — triples the value of the next Nigiri you play
        "Pudding"           — end-of-game: most puddings +6 pts, fewest -6 pts
        "Chopsticks"        — on a future turn, play two cards instead of one

    ── Current turn ─────────────────────────────────────────────────────────
        hand                          — list[str] of cards in your hand right now
        state.hand                    — same list (also passed as first arg)
        state.last_card_played        — card you played last turn (None on first turn)
        state.round                   — current round number: 1, 2, or 3
        state.turn                    — turn number within the current round

    ── Your tableau (cards played in front of you this round) ───────────────
        state.my_tableau              — list[str] of all cards you've played this round
        state.count("Sashimi")        — how many of a specific card are in your tableau
        state.puddings                — int: number of Puddings in your tableau
        state.has_unused_wasabi       — True if you have a Wasabi not yet paired with Nigiri
        state.has_chopsticks          — True if Chopsticks are in your tableau (usable)

    ── Hand rotation tracking ───────────────────────────────────────────────
        state.all_hands               — list of every hand you've held this round;
                                        slots are None until you've seen that hand.
                                        Cards are removed as you play them.
        state.current_hand_ptr        — index of the hand you're currently holding
        state.next_hand               — all_hands[ptr+1]: the hand coming to you
                                        next turn (None if not yet seen)
        state.player_count            — total number of players in this game

    ── Opponent tracking ────────────────────────────────────────────────────
        state.all_tableaux            — {player_name: list[str]} everyone's full
                                        board this round, updated each turn
        state.last_turn_reveals       — {player_name: list[str]} what each player
                                        revealed last turn only
        state.player_name             — your own name (to look yourself up in the dicts)
    """
    # ──────────────────────────────────────────────────────────────────────────
    #  INSERT ALGORITHM HERE
    # ──────────────────────────────────────────────────────────────────────────

    # We do chopstick combo if available before

    # Do best combo
    combos = valid_combo(state, hand, state.my_tableau)
    if combos:
        return hand.index(combos[0]) # valid_combo returns list of card names worth playing, ordered by value
    
    # Grab chopsticks if next hand can combo
    if should_grab_chopsticks(hand, state) and "Chopsticks" in hand:
        return hand.index("Chopsticks")
    
    # Grab pudding if we don't have the most and can get there, or are last
    total_turns = 0
    if state.player_count == 2:
        total_turns = 10
    elif state.player_count == 3:
        total_turns = 9
    elif state.player_count == 4:
        total_turns = 8
    elif state.player_count == 5:
        total_turns = 7
    turns_left = total_turns - state.turn
    pudding_counts = []
    for player, tableau in state.all_tableaux.items():
        count = tableau.count("Pudding")
        pudding_counts.append(count)
    if not pudding_counts:
        pass
    elif (state.puddings < max(pudding_counts) and state.puddings + turns_left > max(pudding_counts)) or (state.puddings <= min(pudding_counts)):
        if "Pudding" in hand:
            return hand.index("Pudding")
    
    # Grab wasabi if next hand has more than one nigiri
    nigiri_types = ["Egg Nigiri", "Salmon Nigiri", "Squid Nigiri"]
    nigiri_count = sum(1 for card in hand if card in nigiri_types)
    if nigiri_count >= 2 and "Wasabi" in hand:
        return hand.index("Wasabi")
    
    # Grab dumplings if less than 5 on tableu
    if state.count("Dumpling") < 5 and "Dumpling" in hand:
        return hand.index("Dumpling")

    # Grab nigiri 
    if "Squid Nigiri" in hand:
        return hand.index("Squid Nigiri")
    if "Salmon Nigiri" in hand:
        return hand.index("Salmon Nigiri")
    if "Egg Nigiri" in hand:
        return hand.index("Egg Nigiri")
    
    # Grab sashimi if one on table
    if state.count("Sashimi") == 1 and "Sashimi" in hand:
        return hand.index("Sashimi")
    
    # Grab tempura
    if state.count("Tempura") == 1 and "Tempura" in hand:
        return hand.index("Tempura")
    
    # Grab sashimi
    if "Sashimi" in hand:
        return hand.index("Sashimi")
    
    # Grab maki rolls
    if "Maki Roll (3)" in hand:
        return hand.index("Maki Roll (3)")
    if "Maki Roll (2)" in hand:
        return hand.index("Maki Roll (2)")
    if "Maki Roll (1)" in hand:
        return hand.index("Maki Roll (1)")
    
    return 0

def valid_combo(state: GameState, hand: list[str], my_table: list[str]) -> list[str]:
    """
    Returns a list of card names worth playing this turn as a single play.
    Ordered by point value (highest first):
      Sashimi triple  = 10 pts
      Squid + Wasabi  =  9 pts
      Salmon + Wasabi =  6 pts
      Tempura pair    =  5 pts
      Egg + Wasabi    =  3 pts

    Also see chopstick_combo() for 2-card plays when Chopsticks are available,
    and should_grab_chopsticks() for deciding whether to pick them up this turn.
    """
    res = []

    # Sashimi triple (10 pts) — need exactly 2 already down
    if occurances(my_table, "Sashimi") % 3 == 2 and "Sashimi" in hand:
        res.append("Sashimi")

    # Squid Nigiri on unused Wasabi (9 pts)
    if state.has_unused_wasabi and "Squid Nigiri" in hand:
        res.append("Squid Nigiri")

    # Salmon Nigiri on unused Wasabi (6 pts)
    if state.has_unused_wasabi and "Salmon Nigiri" in hand:
        res.append("Salmon Nigiri")

    # Tempura pair (5 pts) — need exactly 1 already down
    if occurances(my_table, "Tempura") % 2 == 1 and "Tempura" in hand:
        res.append("Tempura")

    # Egg Nigiri on unused Wasabi (3 pts)
    if state.has_unused_wasabi and "Egg Nigiri" in hand:
        res.append("Egg Nigiri")

    return res

def chopstick_combo(hand: list[str], state: GameState) -> Optional[tuple[int, int]]:
    """
    If Chopsticks are in our tableau, find the best 2-card play for this turn.
    Returns (idx1, idx2) where idx1 is played first, or None to skip chopsticks.

    Wasabi MUST be idx1 so the server places it before the Nigiri — otherwise
    the triple bonus won't apply.

    Priority (same order as valid_combo):
      1. Two Sashimi   (10 pts) — need exactly 1 in tableau, play 2 to finish triple
      2. Wasabi + Squid Nigiri  ( 9 pts)
      3. Wasabi + Salmon Nigiri ( 6 pts)
      4. Two Tempura   ( 5 pts) — need exactly 1 in tableau, play 2 to finish pair
      5. Wasabi + Egg Nigiri    ( 3 pts)
    """
    if not state.has_chopsticks:
        return None

    # Sashimi triple (10 pts) — need exactly 1 already down, play 2 from hand to finish
    if occurances(state.my_tableau, "Sashimi") % 3 == 1:
        sashimi_indices = [i for i, c in enumerate(hand) if c == "Sashimi"]
        if len(sashimi_indices) >= 2:
            return (sashimi_indices[0], sashimi_indices[1])

    # Wasabi + Squid Nigiri (9 pts) — Wasabi must be idx1
    if "Wasabi" in hand and "Squid Nigiri" in hand:
        return (hand.index("Wasabi"), hand.index("Squid Nigiri"))

    # Wasabi + Salmon Nigiri (6 pts) — Wasabi must be idx1
    if "Wasabi" in hand and "Salmon Nigiri" in hand:
        return (hand.index("Wasabi"), hand.index("Salmon Nigiri"))

    # Tempura pair (5 pts) — need exactly 1 already down, play 2 from hand to finish
    if occurances(state.my_tableau, "Tempura") % 2 == 1:
        tempura_indices = [i for i, c in enumerate(hand) if c == "Tempura"]
        if len(tempura_indices) >= 2:
            return (tempura_indices[0], tempura_indices[1])

    # Wasabi + Egg Nigiri (3 pts) — Wasabi must be idx1
    if "Wasabi" in hand and "Egg Nigiri" in hand:
        return (hand.index("Wasabi"), hand.index("Egg Nigiri"))

    return None


def should_grab_chopsticks(hand: list[str], state: GameState) -> bool:
    """
    Returns True if we should pick up Chopsticks this turn because the next
    hand (state.next_hand) contains a combo we can cash in with them.

    Only relevant when:
      - "Chopsticks" is available in the current hand
      - We don't already have Chopsticks in our tableau
      - state.next_hand is known (not None)

    Combos that make grabbing Chopsticks worthwhile:
      - Next hand has Wasabi + any Nigiri  (play them together for the triple)
      - Next hand has 2+ Sashimi
      - Next hand has 2+ Tempura
    """
    if "Chopsticks" not in hand:
        return False
    if state.has_chopsticks:       # already have chopsticks in tableau
        return False

    next_h = state.next_hand
    if next_h is None:             # haven't seen the next hand yet
        return False

    nigiri_types = ["Egg Nigiri", "Salmon Nigiri", "Squid Nigiri"]
    # Wasabi + Nigiri in next hand → use chopsticks to play them together
    if "Wasabi" in next_h and any(n in next_h for n in nigiri_types):
        return True

    # Two or more Sashimi in next hand
    if next_h.count("Sashimi") >= 2:
        return True

    # Two or more Tempura in next hand
    if next_h.count("Tempura") >= 2:
        return True

    return False


def occurances(hand, card):
    res = 0
    for i in hand:
        if i == card:
            res += 1
    return res

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

    chopstick_combos = chopstick_combo(hand, state) # returns tuple 
    if chopstick_combos:
        return chopstick_combos

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
                player_name=player_name,
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

        # Remove the played card from the copy of this hand we're tracking
        ptr = self.state.current_hand_ptr
        if ptr < len(self.state.all_hands) and self.state.all_hands[ptr] is not None:
            tracked = self.state.all_hands[ptr]
            if card in tracked:
                tracked.remove(card)

    # ── Server message handlers ──────────────────────────────────────────────

    def handle_game_start(self, message: str):
        """GAME_START <player_count> — initialise hand-rotation tracking."""
        parts = message.split()
        if self.state and len(parts) >= 2:
            self.state.player_count = int(parts[1])
            self.state.all_hands = [None] * self.state.player_count
            print(f"Game starting with {self.state.player_count} players")

    def handle_round_start(self, message: str):
        parts = message.split()
        self.state.round = int(parts[1])
        self.state.turn = 1
        self.state.my_tableau = []
        self.state.last_card_played = None
        self.state.last_turn_reveals = {}
        # Reset hand rotation for this round
        self.state.current_hand_ptr = 0
        self.state.all_hands = [None] * max(self.state.player_count, 1)
        self.state.all_tableaux = {}
        print(f"\n{'─'*40}")
        print(f"  ROUND {self.state.round} STARTING")
        print(f"{'─'*40}")

    def handle_played(self, message: str):
        """Cards were revealed — update all tableaux, advance hand pointer."""
        reveals = self.parse_played(message)
        self.state.last_turn_reveals = reveals
        self.state.turn += 1

        # Append each player's played card(s) to their tableau
        for player, cards in reveals.items():
            if player not in self.state.all_tableaux:
                self.state.all_tableaux[player] = []
            self.state.all_tableaux[player].extend(cards)

        # Keep my_tableau in sync with all_tableaux for our own name
        our_name = self.state.player_name
        if our_name in self.state.all_tableaux:
            self.state.my_tableau = list(self.state.all_tableaux[our_name])

        # Advance pointer — next HAND we receive goes into all_hands[ptr]
        self.state.current_hand_ptr += 1

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

    # ── Game loop (shared by single-game and tournament) ─────────────────────

    def _play_game(self) -> Optional[str]:
        """
        Play one full game.
        Returns a TOURNAMENT_MATCH or TOURNAMENT_COMPLETE message if one arrives
        mid-game (so the tournament loop can act on it), otherwise None.
        """
        while True:
            msg = self.recv()
            if not msg:
                continue

            # Tournament messages can arrive during a game — bubble them up
            if msg.startswith("TOURNAMENT_MATCH") or msg.startswith("TOURNAMENT_COMPLETE"):
                return msg

            if msg.startswith("GAME_END"):
                self.handle_game_end(msg)
                return None
            elif msg.startswith("GAME_START"):
                self.handle_game_start(msg)
            elif msg.startswith("ROUND_START"):
                self.handle_round_start(msg)
            elif msg.startswith("PLAYED"):
                self.handle_played(msg)
            elif msg.startswith("ROUND_END"):
                self.handle_round_end(msg)
            elif msg.startswith("HAND"):
                parsed = self.parse_hand(msg)
                self.state.hand = parsed
                ptr = self.state.current_hand_ptr
                if ptr < len(self.state.all_hands):
                    self.state.all_hands[ptr] = list(parsed)
                self.play_turn()
            # OK, WAITING, JOINED — logged automatically, no action needed

    # ── Single-game entry point ───────────────────────────────────────────────

    def run(self, game_id: str, player_name: str):
        try:
            self.connect()

            if not self.join_game(game_id, player_name):
                return

            self.send("READY")
            self._play_game()

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            self.disconnect()

    # ── Tournament entry point ────────────────────────────────────────────────

    def _join_tournament(self, tournament_id: str, player_name: str) -> bool:
        """Send TOURNEY, wait for TOURNAMENT_WELCOME."""
        self.send(f"TOURNEY {tournament_id} {player_name}")
        response = self.recv_until(
            lambda m: m.startswith("TOURNAMENT_WELCOME") or m.startswith("ERROR")
        )
        if response.startswith("TOURNAMENT_WELCOME"):
            parts = response.split()
            rejoin_token = parts[3] if len(parts) > 3 else ""
            print(f"Joined tournament '{tournament_id}' (rejoin token: {rejoin_token})")
            return True
        print(f"Failed to join tournament: {response}")
        return False

    def _join_match(self, match_token: str, player_name: str) -> bool:
        """Send TJOIN, wait for WELCOME, set up game state for the new match."""
        self.send(f"TJOIN {match_token}")
        response = self.recv_until(
            lambda m: m.startswith("WELCOME") or m.startswith("ERROR")
        )
        if response.startswith("WELCOME"):
            parts = response.split()
            self.state = GameState(
                game_id=parts[1],
                player_id=int(parts[2]),
                rejoin_token=parts[3] if len(parts) > 3 else "",
                player_name=player_name,
            )
            print(f"Joined match '{self.state.game_id}' as player {self.state.player_id}")
            return True
        print(f"Failed to join match: {response}")
        return False

    def _leave_game(self):
        """Send LEAVE after a tournament match so we can join the next one."""
        self.send("LEAVE")
        self.recv_until(lambda m: m.startswith("OK") or m.startswith("ERROR"))
        self.state = None

    def run_tournament(self, tournament_id: str, player_name: str):
        try:
            self.connect()

            if not self._join_tournament(tournament_id, player_name):
                return

            pending_message = None

            while True:
                msg = pending_message if pending_message else self.recv()
                pending_message = None

                if not msg:
                    continue

                if msg.startswith("TOURNAMENT_MATCH"):
                    # TOURNAMENT_MATCH <tid> <match_token> <round> [<opponent>]
                    parts = msg.split()
                    match_token = parts[2]
                    round_num   = parts[3]
                    opponent    = parts[4] if len(parts) > 4 else "unknown"

                    if match_token == "BYE" or opponent == "BYE":
                        print(f"Tournament round {round_num}: BYE — auto-advancing")
                        continue

                    print(f"Tournament round {round_num}: vs {opponent}")

                    if not self._join_match(match_token, player_name):
                        continue

                    self.send("READY")

                    # Play the game; may return a tournament message that arrived mid-game
                    pending_message = self._play_game()

                    self._leave_game()

                elif msg.startswith("TOURNAMENT_COMPLETE"):
                    parts = msg.split()
                    winner = parts[2] if len(parts) > 2 else "unknown"
                    print(f"Tournament complete! Winner: {winner}")
                    break

                elif msg.startswith("TOURNAMENT_JOINED"):
                    print(f"  {msg}")

                # Ignore other messages (TOURNAMENT_WELCOME extras, etc.)

        except KeyboardInterrupt:
            print("\nDisconnecting...")
        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            self.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  TESTS  —  run with: python bot_01.py --test
# ══════════════════════════════════════════════════════════════════════════════

def run_tests():
    passed = 0
    failed = 0

    def check(label: str, actual, expected):
        nonlocal passed, failed
        if actual == expected:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label}")
            print(f"        expected: {expected!r}")
            print(f"        got:      {actual!r}")
            failed += 1

    print("\n── Test: hand rotation tracking ─────────────────────────────")

    # Build a bot and a fake state (no real network connection needed)
    bot = SushiGoBot.__new__(SushiGoBot)
    bot.state = GameState(
        game_id="test",
        player_id=0,
        rejoin_token="",
        player_name="Alice",
        player_count=3,
        all_hands=[None, None, None],
        current_hand_ptr=0,
    )

    # Simulate ROUND_START 1
    bot.handle_round_start("ROUND_START 1")
    check("round resets to 1", bot.state.round, 1)
    check("ptr resets to 0", bot.state.current_hand_ptr, 0)
    check("all_hands has 3 slots", len(bot.state.all_hands), 3)
    check("all_hands starts empty", bot.state.all_hands, [None, None, None])

    # Simulate receiving first HAND
    hand1 = ["Tempura", "Sashimi", "Salmon Nigiri", "Pudding"]
    hand1_msg = "HAND 0:Tempura 1:Sashimi 2:Salmon Nigiri 3:Pudding"
    parsed = bot.parse_hand(hand1_msg)
    bot.state.hand = parsed
    bot.state.all_hands[bot.state.current_hand_ptr] = list(parsed)

    check("hand parsed correctly", bot.state.hand, hand1)
    check("all_hands[0] populated", bot.state.all_hands[0], hand1)
    check("next_hand is None (unseen)", bot.state.next_hand, None)

    # Simulate playing Tempura (index 0)
    bot._record_play("Tempura")
    check("last_card_played = Tempura", bot.state.last_card_played, "Tempura")
    check("my_tableau has Tempura", bot.state.my_tableau, ["Tempura"])
    check("all_hands[0] has Tempura removed", bot.state.all_hands[0], ["Sashimi", "Salmon Nigiri", "Pudding"])

    # Simulate PLAYED — everyone reveals their card
    played_msg = "PLAYED Alice:Tempura; Bob:Sashimi; Carol:Dumpling"
    bot.handle_played(played_msg)
    check("ptr advances to 1", bot.state.current_hand_ptr, 1)
    check("all_tableaux[Alice]", bot.state.all_tableaux.get("Alice"), ["Tempura"])
    check("all_tableaux[Bob]", bot.state.all_tableaux.get("Bob"), ["Sashimi"])
    check("all_tableaux[Carol]", bot.state.all_tableaux.get("Carol"), ["Dumpling"])
    check("last_turn_reveals correct", bot.state.last_turn_reveals, {
        "Alice": ["Tempura"], "Bob": ["Sashimi"], "Carol": ["Dumpling"]
    })

    # Simulate receiving second HAND (the rotated hand)
    hand2 = ["Maki Roll (3)", "Dumpling", "Wasabi"]
    hand2_msg = "HAND 0:Maki Roll (3) 1:Dumpling 2:Wasabi"
    parsed2 = bot.parse_hand(hand2_msg)
    bot.state.hand = parsed2
    bot.state.all_hands[bot.state.current_hand_ptr] = list(parsed2)

    check("all_hands[1] populated", bot.state.all_hands[1], hand2)
    check("next_hand is None (turn 3 unseen)", bot.state.next_hand, None)
    check("ptr still 1", bot.state.current_hand_ptr, 1)

    # Simulate playing Wasabi (index 2)
    bot._record_play("Wasabi")
    check("last_card_played = Wasabi", bot.state.last_card_played, "Wasabi")
    check("has_unused_wasabi = True", bot.state.has_unused_wasabi, True)
    check("all_hands[1] has Wasabi removed", bot.state.all_hands[1], ["Maki Roll (3)", "Dumpling"])

    # Simulate PLAYED turn 2
    bot.handle_played("PLAYED Alice:Wasabi; Bob:Maki Roll (2); Carol:Sashimi")
    check("ptr advances to 2", bot.state.current_hand_ptr, 2)
    check("all_tableaux[Alice] has both cards", bot.state.all_tableaux.get("Alice"), ["Tempura", "Wasabi"])

    print("\n── Test: parse_played with multiple cards (chopsticks) ───────")
    reveals = bot.parse_played("PLAYED Alice:Squid Nigiri,Tempura; Bob:Sashimi")
    check("chopsticks play parsed", reveals.get("Alice"), ["Squid Nigiri", "Tempura"])
    check("single card still works", reveals.get("Bob"), ["Sashimi"])

    print(f"\n{'─'*40}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'─'*40}\n")
    return failed == 0


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args(args):
    """Parse [host port] id name or id name [host] [port] into (host, port, id, name)."""
    host, port = "localhost", 7878
    if len(args) >= 4 and args[1].isdigit():
        host, port = args[0], int(args[1])
        return host, port, args[2], args[3]
    else:
        id_, name = args[0], args[1]
        if len(args) > 2:
            host = args[2]
        if len(args) > 3:
            port = int(args[3])
        return host, port, id_, name


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--test":
        success = run_tests()
        sys.exit(0 if success else 1)

    # Tournament mode: python royale_bot.py --tournament <tournament_id> <player_name> [host] [port]
    if len(sys.argv) >= 2 and sys.argv[1] == "--tournament":
        args = sys.argv[2:]
        if len(args) < 2:
            print("Usage: python royale_bot.py --tournament <tournament_id> <player_name> [host] [port]")
            sys.exit(1)
        host, port, tournament_id, player_name = _parse_args(args)
        SushiGoBot(host, port).run_tournament(tournament_id, player_name)
        return

    # Single-game mode (existing behaviour)
    if len(sys.argv) < 3:
        print("Usage: python royale_bot.py <game_id> <player_name> [host] [port]")
        print("   or: python royale_bot.py <host> <port> <game_id> <player_name>")
        print("   or: python royale_bot.py --tournament <tournament_id> <player_name> [host] [port]")
        print("   or: python royale_bot.py --test")
        sys.exit(1)

    host, port, game_id, player_name = _parse_args(sys.argv[1:])
    SushiGoBot(host, port).run(game_id, player_name)


if __name__ == "__main__":
    main()
