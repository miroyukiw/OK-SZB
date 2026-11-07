"""Win-rate optimization telemetry & post-match attribution for Dragon Tempo (Pack 10010)."""
import json
import time
from collections import Counter
from pathlib import Path
from swb.policy import (
    BANDIT, TANTRUM, VORALAI, CHIMICA, RAIDER, FANGS, DRAGOON, SCOUT,
    CARRIER, BLADE_GRANT, OUTLAW, GILVA, KIRITSU, ANTIMARIA,
    FIRE_DRAKE, ABYSS, RED_TORRENT,
)

COMBO_CARDS = {BANDIT, RAIDER, DRAGOON, ANTIMARIA}
CLASS_CODE_MAP = {
    '1': 'elf',
    '2': 'royal',
    '3': 'witch',
    '4': 'dragon',
    '5': 'nightmare',
    '6': 'bishop',
    '7': 'nemesis',
}


def infer_class_from_cards(card_counts: dict):
    votes = Counter()
    for cid, cnt in (card_counts or {}).items():
        s = str(cid)
        if len(s) >= 4:
            cls = CLASS_CODE_MAP.get(s[3])
            if cls:
                votes[cls] += cnt
    return votes.most_common(1)[0][0] if votes else None


class DragonTelemetry:
    def __init__(self, root: Path, run_id: str, mode: str):
        self.root = Path(root)
        self.run_id = run_id
        self.mode = mode
        self.out_dir = self.root / 'user_data' / 'dragon_tempo_analysis'
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.mulligan_record = None
        self.turns = []
        self.current_turn = None
        self.discards = []
        self.evolutions = []
        self.hand_buffs = []
        self.enemy_cards_seen = Counter()
        self.enemy_pack10_seen = Counter()
        self.combo_turns_active = 0
        self.combo_turns_total = 0
        self.combo_plays = []
        self.tantrum_plays = []
        self.blade_grant_plays = []
        self.scout_events = {'played': 0, 'attacked_to_buff': 0}
        self.gilva_super_turn = None
        self.close_to_lethal_snapshots = []

    def record_mulligan(self, opening, opponent_class, play_order):
        self.mulligan_record = {
            'opponent_class': opponent_class,
            'play_order': play_order,
            'kept_ids': [c.get('card_id') for c in opening if c.get('keep')],
            'replaced_ids': [c.get('card_id') for c in opening if not c.get('keep')],
            'opening': [
                {
                    'card_id': c.get('card_id'),
                    'cost': c.get('cost'),
                    'keep': c.get('keep'),
                    'reason': c.get('reason'),
                }
                for c in opening
            ],
        }

    def observe_turn_start(self, turn: int, state: dict):
        combo_active = bool(state.get('attacked_leader_last_turn'))
        self.combo_turns_total += 1
        if combo_active:
            self.combo_turns_active += 1

        self.observe_enemy_board(state.get('enemy') or [], turn)
        theoretical_reach = self.estimate_theoretical_reach(state)
        enemy_hp = state.get('enemy_hp')

        rec = {
            'turn': turn,
            'pp_start': state.get('pp'),
            'max_pp': state.get('max_pp'),
            'own_hp_start': state.get('own_hp'),
            'enemy_hp_start': enemy_hp,
            'combo_active': combo_active,
            'hand_ids_start': [c.get('id') for c in (state.get('hand') or [])],
            'own_board_start': [u.get('card_id') for u in (state.get('own') or [])],
            'enemy_board_start': [
                {
                    'card_id': u.get('card_id'),
                    'atk': u.get('attack'),
                    'hp': u.get('health'),
                    'ward': u.get('ward'),
                    'intimidate': u.get('intimidate'),
                    'aura': u.get('aura'),
                }
                for u in (state.get('enemy') or [])
            ],
            'theoretical_reach': theoretical_reach,
            'cards_played': [],
            'attacks_made': 0,
            'leader_attacked': False,
            'pp_end': state.get('pp'),
        }
        self.turns.append(rec)
        self.current_turn = rec

        if enemy_hp is not None and (enemy_hp <= 6 or theoretical_reach >= enemy_hp):
            self.close_to_lethal_snapshots.append({
                'turn': turn,
                'enemy_hp': enemy_hp,
                'theoretical_reach': theoretical_reach,
                'combo_active': combo_active,
                'hand': [
                    {'id': c.get('id'), 'cost': c.get('cost'), 'form': c.get('form')}
                    for c in (state.get('hand') or [])
                ],
                'own_board': [
                    {
                        'card_id': u.get('card_id'),
                        'atk': u.get('attack'),
                        'hp': u.get('health'),
                        'ready': u.get('ready'),
                        'can_face': u.get('can_face'),
                    }
                    for u in (state.get('own') or [])
                ],
                'enemy_wards': [
                    {'card_id': u.get('card_id'), 'atk': u.get('attack'), 'hp': u.get('health')}
                    for u in (state.get('enemy') or [])
                    if u.get('ward')
                ],
                'ep_available': state.get('ep_available'),
                'super_available': state.get('super_available'),
            })

    def observe_enemy_board(self, enemy_units: list, turn: int):
        for u in enemy_units:
            cid = str(u.get('card_id') or '')
            if cid:
                self.enemy_cards_seen[cid] += 1
                if cid.startswith('110') or cid.startswith('900'):
                    self.enemy_pack10_seen[cid] += 1

    def estimate_theoretical_reach(self, state: dict) -> int:
        combo_active = bool(state.get('attacked_leader_last_turn'))
        own = state.get('own') or []
        board_dmg = sum((u.get('attack') or 0) for u in own if u.get('ready') and u.get('can_face'))
        hand = state.get('hand') or []
        pp = state.get('pp') or 0
        hand_storm = 0
        for c in hand:
            cid = c.get('id')
            cost = c.get('cost') or 99
            if cost > pp:
                continue
            if cid == KIRITSU:
                hand_storm = max(hand_storm, 5)
            elif cid == ANTIMARIA and combo_active:
                hand_storm = max(hand_storm, 6)
            elif cid == RAIDER:
                hand_storm = max(hand_storm, 2 if combo_active else 1)
            elif cid == BANDIT and combo_active:
                hand_storm = max(hand_storm, 1)
            elif cid == GILVA and state.get('super_available'):
                hand_storm = max(hand_storm, 7)
        evo_bonus = 3 if state.get('super_available') else (2 if state.get('ep_available') else 0)
        emblem_bonus = 5 if self.gilva_super_turn is not None else 0
        return board_dmg + hand_storm + evo_bonus + emblem_bonus

    def record_play(self, turn: int, card: dict, state_before: dict, pp_after):
        cid = str(card.get('id') or '')
        cost_paid = (
            (state_before.get('pp') - pp_after)
            if (state_before.get('pp') is not None and pp_after is not None)
            else card.get('cost')
        )
        combo_active = bool(state_before.get('attacked_leader_last_turn'))
        entry = {
            'turn': turn,
            'card_id': cid,
            'form': card.get('form'),
            'cost_paid': cost_paid,
            'combo_active': combo_active,
        }
        if self.current_turn:
            self.current_turn['cards_played'].append(entry)
            if pp_after is not None:
                self.current_turn['pp_end'] = pp_after

        if cid in COMBO_CARDS:
            self.combo_plays.append(entry)
        if cid == TANTRUM:
            self.tantrum_plays.append(entry)
        if cid == BLADE_GRANT:
            self.blade_grant_plays.append(entry)
        if cid == SCOUT:
            self.scout_events['played'] += 1

    def record_discard(self, turn: int, source_cid: str, selected_cards: list, all_candidates: list):
        discarded_ids = [c.get('id') for c in selected_cards]
        candidate_ids = [c.get('id') for c in all_candidates]
        ocean_synergy = any(cid in (ABYSS, BANDIT, VORALAI, BLADE_GRANT) for cid in discarded_ids)
        high_value_discarded = [cid for cid in discarded_ids if cid in (KIRITSU, GILVA, ANTIMARIA, DRAGOON)]
        self.discards.append({
            'turn': turn,
            'source_card_id': str(source_cid),
            'discarded_ids': discarded_ids,
            'candidate_ids': candidate_ids,
            'ocean_or_bandit_synergy': ocean_synergy,
            'high_value_discarded': high_value_discarded,
        })

    def record_hand_buff(self, turn: int, source_cid: str, chosen_card: dict, candidates: list):
        self.hand_buffs.append({
            'turn': turn,
            'source_card_id': str(source_cid),
            'buffed_card_id': chosen_card.get('id'),
            'buffed_cost': chosen_card.get('cost'),
            'candidate_ids': [c.get('id') for c in candidates],
        })

    def record_evolution(self, turn: int, kind: str, unit: dict, confirmed: bool):
        cid = str(unit.get('card_id') or '')
        self.evolutions.append({
            'turn': turn,
            'kind': kind,
            'card_id': cid,
            'confirmed': confirmed,
        })
        if confirmed and kind == 'super_evolve' and cid == GILVA and self.gilva_super_turn is None:
            self.gilva_super_turn = turn

    def record_attack(self, turn: int, unit: dict, target: list):
        cid = str(unit.get('card_id') or '')
        is_face = (target == [640, 80])
        if self.current_turn:
            self.current_turn['attacks_made'] += 1
            if is_face:
                self.current_turn['leader_attacked'] = True
        if cid == SCOUT:
            self.scout_events['attacked_to_buff'] += 1

    def record_turn_end(self, state: dict):
        if self.current_turn and state:
            if state.get('pp') is not None:
                self.current_turn['pp_end'] = state.get('pp')
            self.current_turn['enemy_hp_end'] = state.get('enemy_hp')
            self.current_turn['own_hp_end'] = state.get('own_hp')

    def build_summary(self, outcome: str, last_state: dict, opponent_class: str, play_order: str) -> dict:
        last_state = last_state or {}
        if not opponent_class:
            opponent_class = infer_class_from_cards(self.enemy_cards_seen)
            if self.mulligan_record and not self.mulligan_record.get('opponent_class'):
                self.mulligan_record['opponent_class'] = opponent_class
        wasted_pp_total = sum(max(0, (t.get('pp_end') or 0)) for t in self.turns)
        unactivated_combo_plays = [p for p in self.combo_plays if not p.get('combo_active')]
        tantrum_1pp = sum(1 for p in self.tantrum_plays if (p.get('cost_paid') or 1) == 1)
        tantrum_3pp = sum(1 for p in self.tantrum_plays if (p.get('cost_paid') or 0) >= 3)
        blade_2pp = sum(1 for p in self.blade_grant_plays if (p.get('cost_paid') or 4) <= 2)
        blade_4pp = sum(1 for p in self.blade_grant_plays if (p.get('cost_paid') or 4) > 2)

        missed_lethal_suspect = bool(
            outcome == 'loss'
            and any((s.get('enemy_hp') or 99) <= 4 or (s.get('theoretical_reach') or 0) >= (s.get('enemy_hp') or 99)
                    for s in self.close_to_lethal_snapshots)
        )
        unspent_evo_on_loss = bool(
            outcome == 'loss'
            and ((last_state.get('ep_left') or 0) > 0 or (last_state.get('sep_left') or 0) > 0)
        )

        telemetry = {
            'deck_profile': 'dragon_tempo',
            'outcome': outcome,
            'opponent_class': opponent_class,
            'play_order': play_order,
            'total_turns': len(self.turns),
            'mulligan': self.mulligan_record,
            'combo_chain': {
                'active_turns': self.combo_turns_active,
                'total_turns': self.combo_turns_total,
                'activation_rate': round(self.combo_turns_active / max(1, self.combo_turns_total), 3),
                'combo_plays_total': len(self.combo_plays),
                'unactivated_combo_plays': unactivated_combo_plays,
            },
            'discard_audit': {
                'total_discards': len(self.discards),
                'ocean_or_bandit_synergy_count': sum(1 for d in self.discards if d['ocean_or_bandit_synergy']),
                'high_value_discarded_events': [d for d in self.discards if d['high_value_discarded']],
                'events': self.discards,
            },
            'evolution_roi': {
                'evolutions': self.evolutions,
                'scout_dragonnewt': self.scout_events,
                'carrier_hand_buffs': self.hand_buffs,
                'gilva_super_turn': self.gilva_super_turn,
                'unspent_evo_on_loss': unspent_evo_on_loss,
                'ep_left_end': last_state.get('ep_left'),
                'sep_left_end': last_state.get('sep_left'),
            },
            'pp_and_cost_efficiency': {
                'wasted_pp_total': wasted_pp_total,
                'tantrum_1pp_count': tantrum_1pp,
                'tantrum_3pp_enhance_count': tantrum_3pp,
                'blade_grant_2pp_discounted_count': blade_2pp,
                'blade_grant_4pp_full_count': blade_4pp,
            },
            'lethal_and_reach_audit': {
                'final_enemy_hp': last_state.get('enemy_hp'),
                'final_own_hp': last_state.get('own_hp'),
                'missed_lethal_suspect': missed_lethal_suspect,
                'close_to_lethal_snapshots': self.close_to_lethal_snapshots,
            },
            'meta_and_pack10_threats': {
                'enemy_cards_seen': dict(self.enemy_cards_seen),
                'enemy_pack10_seen': dict(self.enemy_pack10_seen),
            },
        }
        self._persist_match_record(telemetry)
        return {'winrate_telemetry': telemetry}

    def _persist_match_record(self, telemetry: dict):
        try:
            matches_file = self.out_dir / 'matches.jsonl'
            row = {'run_id': self.run_id, 'timestamp': time.time(), 'mode': self.mode, **telemetry}
            with matches_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
            self._update_aggregate_report(matches_file)
        except OSError:
            pass

    def _update_aggregate_report(self, matches_file: Path):
        rows = []
        for line in matches_file.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        if not rows:
            return
        completed = [r for r in rows if r.get('outcome') in ('win', 'loss')]
        wins = sum(1 for r in completed if r.get('outcome') == 'win')
        by_class = {}
        pack10_losses = Counter()
        for r in completed:
            cls = r.get('opponent_class') or 'unknown'
            st = by_class.setdefault(cls, {'matches': 0, 'wins': 0, 'losses': 0})
            st['matches'] += 1
            if r['outcome'] == 'win':
                st['wins'] += 1
            else:
                st['losses'] += 1
                for cid in (r.get('meta_and_pack10_threats') or {}).get('enemy_pack10_seen', {}):
                    pack10_losses[cid] += 1
        for st in by_class.values():
            st['win_rate'] = round(st['wins'] / max(1, st['matches']), 3)

        report = {
            'total_recorded': len(rows),
            'completed_matches': len(completed),
            'wins': wins,
            'losses': len(completed) - wins,
            'overall_win_rate': round(wins / max(1, len(completed)), 3),
            'by_opponent_class': by_class,
            'avg_combo_activation_rate': round(
                sum((r.get('combo_chain') or {}).get('activation_rate', 0) for r in rows) / len(rows), 3
            ),
            'missed_lethal_suspect_count': sum(
                1 for r in completed if (r.get('lethal_and_reach_audit') or {}).get('missed_lethal_suspect')
            ),
            'unspent_evo_on_loss_count': sum(
                1 for r in completed if (r.get('evolution_roi') or {}).get('unspent_evo_on_loss')
            ),
            'top_pack10_cards_in_losses': dict(pack10_losses.most_common(15)),
        }
        (self.out_dir / 'optimization_report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
        )
