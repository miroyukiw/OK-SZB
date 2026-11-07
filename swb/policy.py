"""Combat policy for Rotation Discard/Tempo Dragon."""

# Main deck card IDs (14 cards, 40 copies)
BANDIT = '10941110'       # 碎裂的盗匪 (1c 1/1: prev-turn leader hit -> Storm; Last Words -> add Bandit without Last Words)
TANTRUM = '10941310'      # 幼龙闹脾气 (1c Spell: summon 1/1 Intimidate Fire Drake; Enhance 3 -> deal 3 random enemy follower dmg)
VORALAI = '10644120'      # 古旧天刀·波菈莱 (2c 0/2 Bane: when discarded -> summon Voralai; Evo -> +1 Abyss; Super Evo -> +3 Abyss)
CHIMICA = '10842120'      # 满面笑容的烹饪·琪米卡 (2c 2/1: Fanfare & Evo -> discard 1, draw 1, heal leader 1)
RAIDER = '10942110'       # 绝倒的袭击者 (2c 1/2 Storm: Strike -> if prev-turn leader hit, +1/+0 this turn)
FANGS = '10943310'        # 利牙 (2c Spell: distribute 3 dmg to enemy followers; if prev-turn leader hit -> distribute 6 dmg)
DRAGOON = '10641110'      # 决断的龙人 (3c 2/3: Fanfare -> discard 1; Last Words -> draw 2)
SCOUT = '11042120'        # 侦察龙人 (3c 1/3 Stealth: Strike -> randomly evolve 1 other unevolved allied follower)
CARRIER = '10741120'      # 载运飞龙 (4c 3/2: Fanfare -> give 1 other allied follower on board +2/+2; Evo -> give 1 follower in hand +2/+2)
BLADE_GRANT = '10641310'  # 天刀授予 (4c Spell: when discarded at cost 4 -> add 2c Blade Grant to hand; give 1 allied follower on board +2/+2)
OUTLAW = '10943110'       # 尘土的不法者 (5c 4/4: Fanfare -> 4 random enemy follower dmg, 2x if prev-turn leader hit; Evo -> summon Raider)
GILVA = '11044120'        # 龙惧·吉尔沃 (6c 4/4: Fanfare -> distribute (20 - enemy_hp) dmg to enemy followers; Super Evo -> gain Storm & Ward)
KIRITSU = '10644110'      # 断头的斩姬·相枛津 (7c 5/4 Storm/Bane/Aura: Fanfare -> discard 1, add 2 Red Torrents to hand)
ANTIMARIA = '10944110'    # 穿孔的罪人·安缇马丽亚 (7c 6/5 Rush/Ignore Ward: Fanfare -> if prev-turn leader hit, gain Storm)

# Token card IDs (3 cards)
FIRE_DRAKE = '90041110'   # 炽炎幼龙 (1c 1/1 Intimidate)
ABYSS = '90044330'        # 天刀深渊 (2c Spell: when discarded OR played -> deal 1 dmg to enemy leader, heal own leader 1)
RED_TORRENT = '10642310'  # 赤流 (1c Spell: discard 1 card, destroy 1 enemy follower)

FACE = [640, 80]

FOLLOWER_IDS = {
    BANDIT, VORALAI, CHIMICA, RAIDER, DRAGOON, SCOUT,
    CARRIER, OUTLAW, GILVA, KIRITSU, ANTIMARIA, FIRE_DRAKE,
}
DISCARD_FODDER_T0 = {ABYSS, VORALAI, BLADE_GRANT}
DISCARD_PLAY_SOURCES = {CHIMICA, DRAGOON, KIRITSU, RED_TORRENT}


def effective_card_cost(card, pp=None):
    cid = card.get('id') or card.get('card_id')
    cost = card.get('cost')
    if cid == TANTRUM and pp is not None and pp >= 3:
        return 3
    return cost


def attacker_face_damage(unit, state):
    """Calculate actual damage a ready allied follower deals when attacking enemy leader."""
    atk = unit.get('attack') or 0
    if atk <= 0:
        return 0
    if unit.get('card_id') == RAIDER and state.get('attacked_leader_last_turn'):
        atk += 1
    return atk


def attacker_strike_attack(unit, state):
    """Calculate actual attack power when attacking (including Raider's Strike +1/+0)."""
    atk = unit.get('attack') or 0
    if unit.get('card_id') == RAIDER and state.get('attacked_leader_last_turn'):
        atk += 1
    return atk


def _blocked_set(state):
    res = set()
    for item in (state.get('blocked_attacks') or ()):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            uid, pt = item
            if isinstance(pt, (list, tuple)) and len(pt) == 2:
                res.add((uid, (pt[0], pt[1])))
    return res


def can_unit_hit_face(unit, state, ignore_wards=False):
    if not unit.get('ready') or not unit.get('can_face'):
        return False
    if unit.get('uid') in set(state.get('attacked_uids') or ()):
        return False
    if (unit.get('uid'), tuple(FACE)) in _blocked_set(state):
        return False
    atk = attacker_face_damage(unit, state)
    if atk <= 0:
        return False
    if ignore_wards or unit.get('ignore_ward') or unit.get('card_id') == ANTIMARIA:
        return True
    enemy = state.get('enemy') or []
    return not any(e.get('ward') and e.get('attackable', True) for e in enemy)


def mulligan_plan(opening, opponent_class=None, play_order=None):
    """Mulligan for early 1-3PP curve, leader-hit enablers, and discard synergy."""
    ids = [c.get('card_id') for c in opening]
    has_early_drop = any(cid in (TANTRUM, BANDIT, RAIDER, CHIMICA) for cid in ids)
    has_discarder = any(cid in (CHIMICA, DRAGOON) for cid in ids)
    has_chimica = CHIMICA in ids
    has_blade_grant = BLADE_GRANT in ids
    kept_ids = set()
    kept_fodder = False

    for card in opening:
        cid = card.get('card_id')
        cost = card.get('cost')
        keep = False
        reason = 'replace_high_or_situational'

        if cid == TANTRUM and TANTRUM not in kept_ids:
            keep, reason = True, 'keep_t1_intimidate_drake_enabler'
        elif cid == RAIDER and list(ids).count(RAIDER) <= 2 and RAIDER not in kept_ids:
            keep, reason = True, 'keep_2pp_storm_enabler'
        elif cid == SCOUT and SCOUT not in kept_ids and (play_order != 'second' or has_early_drop):
            keep, reason = True, 'keep_3pp_stealth_free_evolver'
        elif cid == BANDIT and BANDIT not in kept_ids and (play_order == 'first' or not has_early_drop):
            keep, reason = True, 'keep_1pp_curve_bandit'
        elif cid == CHIMICA and CHIMICA not in kept_ids:
            keep, reason = True, 'keep_2pp_discard_cycle'
        elif cid == DRAGOON and DRAGOON not in kept_ids and (has_early_drop or has_blade_grant):
            keep, reason = True, 'keep_3pp_discard_draw_engine'
        elif cid == BLADE_GRANT and has_discarder and not kept_fodder:
            keep, reason = True, 'keep_discard_payoff_with_enabler'
            kept_fodder = True
        elif cid == VORALAI and has_chimica and not has_blade_grant and not kept_fodder:
            keep, reason = True, 'keep_voralai_only_with_2pp_chimica'
            kept_fodder = True
        elif cid == FANGS and FANGS not in kept_ids and play_order == 'second' and opponent_class in ('royal', 'nightmare', 'elf') and has_early_drop:
            keep, reason = True, 'keep_2pp_aoe_going_second_vs_aggro'
        elif cid is None and cost in (1, 2) and not kept_ids:
            keep, reason = True, 'keep_low_cost_fallback'

        if keep and cid:
            kept_ids.add(cid)
        card['keep'] = keep
        card['reason'] = reason
    return opening


def discard_rank(card, state=None, total_candidates=1, source_card_id=None, counts=None):
    """Lower tuple = higher priority to be discarded."""
    state = state or {}
    counts = counts or {}
    cid = card.get('id') or card.get('card_id')
    cost = card.get('cost')
    own_count = len(state.get('own') or [])
    enemy = state.get('enemy') or []
    combo_active = bool(state.get('attacked_leader_last_turn'))
    pp_left = state.get('pp') or 0
    protected = set(state.get('lethal_components') or ())

    if cid in protected and counts.get(cid, 1) <= 1:
        return (9, cost or 0)

    # T0: Net-positive discard payoffs
    if cid == ABYSS:
        # Deals 1 damage to enemy leader and heals 1 when discarded!
        if state.get('enemy_hp') is not None and state.get('enemy_hp') <= 1:
            return (-2, 0)
        return (0, 0)
    if cid == BLADE_GRANT and (cost is None or cost >= 4):
        # Discarding 4c Blade Grant adds a 2c Blade Grant back to hand (free cycle + -2 cost!)
        return (0, 1)
    if cid == VORALAI and own_count < 5:
        # Discarding Voralai summons a 0/2 Bane follower onto the board for 0 PP!
        return (0, 2)

    # T1: Low-cost situational or redundant tokens/fodder
    if cid == RED_TORRENT and not enemy:
        return (1, 0)
    if cid == BANDIT and (not combo_active or pp_left <= 1 or counts.get(BANDIT, 1) > 1):
        return (1, 1)
    if cid == VORALAI:
        # Board is full, still a 0-attack card
        return (1, 2)
    if cid == FANGS and not enemy:
        return (1, 3)

    # T2: Duplicate high-cost cards or unplayable copies
    if counts.get(cid, 1) > 1 and cid in (KIRITSU, ANTIMARIA, GILVA, OUTLAW, CARRIER, DRAGOON, CHIMICA):
        return (2, -(cost or 0))
    if cid == TANTRUM and (state.get('max_pp') or 1) >= 5:
        return (2, 1)
    if cid == BLADE_GRANT:
        return (2, 2)

    # T3: Cards too expensive for current turn
    if cost is not None and (state.get('max_pp') or 0) + 1 < cost and cid not in (ANTIMARIA, KIRITSU):
        return (3, -(cost or 0))

    # T4: Non-storm / utility cards before core storm finishers
    if cid in (DRAGOON, CHIMICA, CARRIER, FANGS, TANTRUM):
        return (4, cost or 0)
    if cid in (OUTLAW, SCOUT, GILVA):
        return (5, cost or 0)
    if cid in (RAIDER, KIRITSU, ANTIMARIA):
        return (6, cost or 0)

    return (4, cost if cost is not None else 9)


def discard_plan(candidates, state=None, total_candidates=None, source_card_id=None):
    state = state or {}
    counts = {}
    for c in candidates:
        cid = c.get('id') or c.get('card_id')
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    ranked = sorted(
        candidates,
        key=lambda c: (
            discard_rank(c, state, total_candidates or len(candidates), source_card_id, counts),
            c['xy'][0],
        ),
    )
    for idx, c in enumerate(ranked):
        c['discard_order'] = idx + 1
    return ranked


def hand_buff_plan(candidates, state=None):
    """Rank follower cards in hand to receive +2/+2 from Carrier Dragon's Evolution."""
    state = state or {}
    combo_active = bool(state.get('attacked_leader_last_turn'))
    pp_left = state.get('pp') or 0
    max_pp = state.get('max_pp') or 4

    def rank(card):
        cid = card.get('id') or card.get('card_id')
        cost = card.get('cost') or 9
        # T0: Playable Storm follower this very turn!
        if cid == BANDIT and combo_active and pp_left >= 1:
            return (0, 1)
        if cid == RAIDER and pp_left >= 2:
            return (0, 2)
        # T1: Low-cost immediate / next-turn guaranteed face attackers (Raider & Stealth Scout)
        if cid == RAIDER:
            return (1, 1)
        if cid == SCOUT:
            return (1, 2)
        # On Turn 5+ (max_pp >= 5), 7PP Storm finishers are only 1-2 turns away; earlier than Turn 5 they clog hand
        if cid == ANTIMARIA:
            return (1, 3) if max_pp >= 5 else (2, 2)
        if cid == KIRITSU:
            return (1, 4) if max_pp >= 5 else (2, 3)
        if cid == BANDIT:
            return (1, 5)
        if cid == GILVA:
            return (2, 1)
        # T3: Tempo bodies
        if cid in (OUTLAW, DRAGOON, CHIMICA, CARRIER):
            return (3, cost)
        # T4: Voralai is usually discarded rather than played from hand
        if cid == VORALAI:
            return (5, cost)
        return (4, cost)

    return sorted(candidates, key=lambda c: (rank(c), c['xy'][0]))


def choose_own_board_target(candidates, card_id, state):
    """Choose the best allied follower on board to receive +2/+2 from Carrier Dragon (10741120) or Blade Grant (10641310)."""
    if not candidates:
        return None
    state = state or {}
    attacked = set(state.get('attacked_uids') or ())
    sep_ready = bool(state.get('super_available'))

    def score(u):
        uid = u.get('uid')
        cid = u.get('card_id')
        atk = u.get('attack') or 0
        hp = u.get('health') or 0
        can_hit_face_now = can_unit_hit_face(u, state)
        can_attack_now = bool(u.get('ready') and uid not in attacked)
        is_stealth_or_intim = bool(u.get('stealth') or u.get('intimidate') or cid in (SCOUT, FIRE_DRAKE))
        is_gilva_super = bool(cid == GILVA and sep_ready and not u.get('super'))
        is_bane = bool(u.get('bane') or cid == VORALAI)
        return (
            1 if can_hit_face_now else 0,
            1 if is_gilva_super else 0,
            1 if is_stealth_or_intim else 0,
            1 if can_attack_now else 0,
            1 if is_bane else 0,
            atk + hp,
            -u['xy'][0],
        )

    return max(candidates, key=score)


def choose_effect_target(targets, card_id, state):
    """Choose enemy follower target for Red Torrent (10642310) or targeted removal."""
    legal = [u for u in targets if not u.get('aura')]
    if not legal:
        return None
    # Prioritize high-HP wards, then highest attack+health threats
    return max(
        legal,
        key=lambda u: (
            1 if u.get('ward') else 0,
            u.get('threat_level', 1),
            (u.get('attack') or 0) * 2 + (u.get('health') or 0),
            -u['xy'][0],
        ),
    )


def choose_mode(state, settings=None):
    return 1


def ocean_summoned(units):
    return units


def has_good_discard_fodder(hand, state, exclude_index=None):
    for i, c in enumerate(hand):
        if i == exclude_index:
            continue
        cid = c.get('id')
        cost = c.get('cost')
        if cid in (ABYSS, VORALAI) or (cid == BLADE_GRANT and (cost is None or cost >= 4)) or cid == BANDIT:
            return True
    return False


def estimate_turn_face_damage(state, pp_override=None):
    """Estimate maximum face damage achievable this turn given current board and hand."""
    pp = state.get('pp', 0) if pp_override is None else pp_override
    enemy = state.get('enemy') or []
    wards = [e for e in enemy if e.get('ward') and e.get('attackable', True)]
    combo_active = bool(state.get('attacked_leader_last_turn'))
    own = state.get('own') or []
    hand = state.get('hand') or []

    # Sum current ready face attackers
    board_face = sum(attacker_face_damage(u, state) for u in own if can_unit_hit_face(u, state, ignore_wards=False))
    pierce_face = sum(attacker_face_damage(u, state) for u in own if can_unit_hit_face(u, state, ignore_wards=True))

    # Scout free-evolve bonus (+2 face damage when Scout attacks and another ready unevolved face attacker exists)
    if not wards and any(u.get('card_id') == SCOUT and can_unit_hit_face(u, state) for u in own):
        if any(u.get('card_id') != SCOUT and not u.get('evolved') and not u.get('super') and can_unit_hit_face(u, state) for u in own):
            board_face += 2

    # Evolution bonus on board or hand storm/outlaw
    evo_bonus = 0
    if state.get('super_available'):
        if any(u.get('card_id') == GILVA and not u.get('super') for u in own):
            evo_bonus = max(evo_bonus, 7 if not wards else 0)
        if pierce_face > 0 or any(c.get('id') == ANTIMARIA and combo_active and (c.get('cost') or 99) <= pp for c in hand):
            evo_bonus = max(evo_bonus, 3)
        if not wards and (board_face > 0 or any(c.get('id') in (RAIDER, KIRITSU) and (c.get('cost') or 99) <= pp for c in hand)):
            evo_bonus = max(evo_bonus, 3)
    elif state.get('ep_available'):
        if any(u.get('card_id') == OUTLAW and not u.get('evolved') for u in own) and not wards:
            evo_bonus = max(evo_bonus, 2 if combo_active else 1)
        if pierce_face > 0 or any(c.get('id') == ANTIMARIA and combo_active and (c.get('cost') or 99) <= pp for c in hand):
            evo_bonus = max(evo_bonus, 2)
        if not wards and (board_face > 0 or any(c.get('id') in (RAIDER, KIRITSU) and (c.get('cost') or 99) <= pp for c in hand)):
            evo_bonus = max(evo_bonus, 2)

    # Hand burst within PP budget
    hand_burst = 0
    rem_pp = pp
    abyss_count = sum(1 for x in hand if x.get('id') == ABYSS)
    used_evo_for_outlaw = False
    for c in sorted(hand, key=lambda x: -(x.get('cost') or 0)):
        cid = c.get('id')
        cost = effective_card_cost(c, rem_pp) or 99
        if cost > rem_pp:
            continue
        if cid == ANTIMARIA and combo_active:
            hand_burst += 6
            rem_pp -= cost
        elif cid == KIRITSU and not wards:
            hand_burst += 5
            if abyss_count > 0:
                hand_burst += 1
                abyss_count -= 1
            rem_pp -= cost
        elif cid == OUTLAW and not wards and not used_evo_for_outlaw and (state.get('ep_available') or state.get('super_available')) and len(own) <= 3:
            # Playing Outlaw + evolving it summons a 1/2 Storm Raider!
            raider_dmg = 2 if combo_active else 1
            if raider_dmg > evo_bonus:
                evo_bonus = raider_dmg
            used_evo_for_outlaw = True
            rem_pp -= cost
        elif cid == RAIDER and not wards:
            hand_burst += 2 if combo_active else 1
            rem_pp -= cost
        elif cid == BANDIT and combo_active and not wards:
            hand_burst += 1
            rem_pp -= cost
        elif cid == BLADE_GRANT and (board_face > 0 or hand_burst > 0) and not wards:
            hand_burst += 2
            rem_pp -= cost
        elif cid == CARRIER and (board_face > 0 or hand_burst > 0) and not wards:
            hand_burst += 2
            rem_pp -= cost
        elif cid in (CHIMICA, DRAGOON, RED_TORRENT) and abyss_count > 0:
            if cid != RED_TORRENT or any(not e.get('aura') for e in enemy):
                hand_burst += 1
                abyss_count -= 1
                rem_pp -= cost
        elif cid == ABYSS and abyss_count > 0:
            hand_burst += 1
            abyss_count -= 1
            rem_pp -= cost

    base = pierce_face if wards else board_face
    return base + evo_bonus + hand_burst


def bonus_pp_plan(state, settings=None):
    """Decide whether to activate the second-player +1 Extra PP gem this turn."""
    extra = state.get('extra_pp') or {}
    if not extra.get('enabled'):
        return None
    turn = state.get('turn') or 1
    phase = 'before_turn6' if turn < 6 else 'turn6_onward'
    if phase in set(state.get('extra_pp_attempted_phases') or ()):
        return None
    pp = state.get('pp')
    hand = state.get('hand') or []
    if pp is None or not hand:
        return None

    target_pp = pp + 1
    enemy_hp = state.get('enemy_hp')
    enemy = state.get('enemy') or []
    own = state.get('own') or []

    # 1. Lethal unlock: +1 PP unlocks lethal this turn!
    if enemy_hp is not None:
        now_dmg = estimate_turn_face_damage(state, pp_override=pp)
        plus_dmg = estimate_turn_face_damage(state, pp_override=target_pp)
        if now_dmg < enemy_hp <= plus_dmg:
            return {'phase': phase, 'reason': 'extra_pp_unlocks_lethal', 'target_pp': target_pp}

    ids_at_target = [c.get('id') for c in hand if (c.get('cost') or 99) == target_pp]
    costs = sorted(
        c.get('cost') or 99
        for c in hand
        if c.get('id') != 'unknown'
        and (c.get('id') != RED_TORRENT or enemy)
        and (c.get('id') != FANGS or enemy)
        and (c.get('id') != BLADE_GRANT or own)
    )
    has_exact_current_curve = any(c == pp for c in costs) or (
        len(costs) >= 2 and any(costs[i] + costs[j] == pp for i in range(len(costs)) for j in range(i + 1, len(costs)))
    )
    has_exact_target_combo = (
        len(costs) >= 2 and any(costs[i] + costs[j] == target_pp for i in range(len(costs)) for j in range(i + 1, len(costs)))
    )

    # 2. Before Turn 6 (1st Extra PP gem: turns 1-5; refreshes at Turn 6 so never waste on Turn 5!)
    if phase == 'before_turn6':
        if target_pp == 2:
            two_drops = [c.get('id') for c in hand if c.get('cost') == 2 and c.get('id') in FOLLOWER_IDS]
            if RAIDER in ids_at_target or (CHIMICA in ids_at_target and has_good_discard_fodder(hand, state)) or len(two_drops) >= 2:
                return {'phase': phase, 'reason': 'unlock_t1_2pp_tempo', 'target_pp': target_pp}
        if target_pp == 3 and any(cid in (SCOUT, DRAGOON) for cid in ids_at_target):
            return {'phase': phase, 'reason': 'unlock_3pp_core_follower', 'target_pp': target_pp}
        if target_pp == 4 and CARRIER in ids_at_target and (own or not has_exact_current_curve):
            return {'phase': phase, 'reason': 'unlock_4pp_carrier_buff', 'target_pp': target_pp}
        if target_pp == 5 and OUTLAW in ids_at_target:
            return {'phase': phase, 'reason': 'unlock_5pp_outlaw_swing', 'target_pp': target_pp}
        if target_pp == 6 and GILVA in ids_at_target:
            return {'phase': phase, 'reason': 'unlock_6pp_gilva', 'target_pp': target_pp}
        if has_exact_target_combo and not has_exact_current_curve:
            return {'phase': phase, 'reason': 'unlock_two_card_tempo_curve', 'target_pp': target_pp}
        if turn == 5 and (ids_at_target or has_exact_target_combo):
            return {'phase': phase, 'reason': 'use_pre_super_extra_pp_before_t6_refresh', 'target_pp': target_pp}

    # 3. Turn 6 onward (2nd Extra PP gem: unlock 7PP Kiritsu / Antimaria on T6 or multi-card burst!)
    else:
        if target_pp == 7 and (KIRITSU in ids_at_target or ANTIMARIA in ids_at_target):
            return {'phase': phase, 'reason': 'unlock_7pp_finisher_on_t6', 'target_pp': target_pp}
        if target_pp == 6 and GILVA in ids_at_target:
            return {'phase': phase, 'reason': 'unlock_6pp_gilva_turn6', 'target_pp': target_pp}
        if has_exact_target_combo and (not has_exact_current_curve or any(c.get('id') in (KIRITSU, ANTIMARIA, GILVA, OUTLAW, CARRIER, RAIDER) for c in hand)):
            return {'phase': phase, 'reason': 'unlock_post_super_combo_pp', 'target_pp': target_pp}
        if target_pp >= 8 and any(c.get('id') in (KIRITSU, ANTIMARIA, GILVA) for c in hand) and len(hand) >= 2:
            return {'phase': phase, 'reason': 'unlock_late_combo_pp', 'target_pp': target_pp}

    return None


def evolve_before_play(state, ordered=None):
    """Decide whether to evolve an existing board follower BEFORE playing cards from hand."""
    if not (state.get('ep_available') or state.get('super_available')):
        return False
    own = state.get('own') or []
    hand = state.get('hand') or []
    pp = state.get('pp') or 0
    combo_active = bool(state.get('attacked_leader_last_turn'))
    unevolved = [u for u in own if not u.get('evolved') and not u.get('super') and not u.get('no_evolve')]
    if not unevolved:
        return False

    # 1. Carrier Dragon (10741120) on board + playable Storm follower in hand -> Evolve Carrier first to buff hand Storm follower!
    if any(u.get('card_id') == CARRIER for u in unevolved):
        if any(
            (c.get('id') == RAIDER and pp >= 2)
            or (c.get('id') == BANDIT and combo_active and pp >= 1)
            or (c.get('id') == KIRITSU and pp >= 7)
            or (c.get('id') == ANTIMARIA and combo_active and pp >= 7)
            for c in hand
        ):
            return True

    # 2. Chimica (10842120) on board + T0 discard fodder (4c Blade Grant / Voralai / Abyss) in hand -> Evolve first to cycle & trigger payoff!
    if any(u.get('card_id') == CHIMICA for u in unevolved) and has_good_discard_fodder(hand, state):
        return True

    # 3. Voralai (10644120) on board + discard card in hand that needs Abyss fodder
    if any(u.get('card_id') == VORALAI for u in unevolved):
        if any(c.get('id') in DISCARD_PLAY_SOURCES and (c.get('cost') or 99) <= pp for c in hand) and not has_good_discard_fodder(hand, state):
            return True

    # 4. Gilva (11044120) on board with Super Evolve available when no better play requires waiting
    if state.get('super_available') and any(u.get('card_id') == GILVA for u in unevolved) and not ordered:
        return True

    return False


def evolution_order(units, state, settings=None):
    """Order allied followers on board for manual evolution."""
    enemy = state.get('enemy') or []
    sep = bool(state.get('super_available'))
    hand = state.get('hand') or []
    max_pp = state.get('max_pp') or 4
    combo_active = bool(state.get('attacked_leader_last_turn'))
    attacked = set(state.get('attacked_uids') or ())
    has_ready_scout = any(
        u.get('card_id') == SCOUT and u.get('ready') and u.get('uid') not in attacked
        for u in units
    )
    unevolved_count = sum(1 for u in units if not u.get('evolved') and not u.get('super') and not u.get('no_evolve'))
    has_fast_hand_buff_target = any(
        c.get('id') in (RAIDER, SCOUT)
        or (c.get('id') == BANDIT and combo_active)
        or (c.get('id') in (ANTIMARIA, KIRITSU) and max_pp >= 5)
        for c in hand
    )

    def priority(u):
        cid = u.get('card_id')
        can_face = can_unit_hit_face(u, state)
        needs_rush = not u.get('ready') and bool(enemy)
        if cid == GILVA and sep:
            # Super-evolving Gilva grants Storm & Ward (+3/+3 -> 7/7 Storm Ward)!
            return (10, u.get('attack') or 0)
        if cid == OUTLAW:
            # Evolving Outlaw summons a 1/2 Storm Raider!
            return (9, u.get('attack') or 0)
        if cid == CARRIER and has_fast_hand_buff_target:
            # Evolving Carrier gives +2/+2 to an immediate/next-turn attacker in hand!
            return (8, u.get('attack') or 0)
        if cid == CHIMICA and len(hand) >= 1 and has_good_discard_fodder(hand, state):
            # Evolving Chimica with good fodder discards 1, draws 1, heals 1!
            return (7, u.get('attack') or 0)
        if can_face:
            # Evolving a ready face attacker adds +2 (or +3) immediate face damage!
            if has_ready_scout and unevolved_count == 2 and cid != SCOUT:
                return (3, u.get('attack') or 0)
            return (6, u.get('attack') or 0)
        if cid == CARRIER and any((c.get('id') in FOLLOWER_IDS) for c in hand):
            return (5, u.get('attack') or 0)
        if cid == CHIMICA and len(hand) >= 1:
            return (5, u.get('attack') or 0)
        if cid == VORALAI:
            return (5, u.get('attack') or 0)
        if needs_rush:
            return (4, u.get('attack') or 0)
        return (1, u.get('attack') or 0)

    return sorted(units, key=priority, reverse=True)


def preferred_evolution(unit, state, available, settings=None):
    """Choose 'super_evolve', 'evolve', or None for the selected unit."""
    if not available or unit.get('evolved') or unit.get('super') or unit.get('no_evolve'):
        return None
    cid = unit.get('card_id')
    enemy = state.get('enemy') or []
    enemy_hp = state.get('enemy_hp')
    own_hp = state.get('own_hp')
    hand = state.get('hand') or []
    can_face = can_unit_hit_face(unit, state)
    face_dmg = attacker_face_damage(unit, state)

    # Check if we should conserve our last Super Evolve point for Gilva / Antimaria / Kiritsu in hand
    sep_left = state.get('sep_left', 1)
    enemy_board_atk = sum(e.get('attack') or 0 for e in enemy)
    must_survive = bool(own_hp is not None and enemy_board_atk >= own_hp and enemy)
    hold_sep_for_finisher = bool(
        'evolve' not in available
        and 'super_evolve' in available
        and sep_left <= 1
        and not can_face
        and not must_survive
        and any(c.get('id') in (GILVA, ANTIMARIA, KIRITSU) for c in hand)
    )

    # 1. Lethal check with evolution
    if can_face and enemy_hp is not None:
        total_now = sum(attacker_face_damage(u, state) for u in (state.get('own') or []) if can_unit_hit_face(u, state))
        if 'super_evolve' in available and total_now + 3 >= enemy_hp:
            return 'super_evolve'
        if 'evolve' in available and total_now + 2 >= enemy_hp:
            return 'evolve'

    # 2. Gilva (11044120): MUST prefer super_evolve to gain Storm & Ward!
    if cid == GILVA:
        if 'super_evolve' in available:
            return 'super_evolve'
        if 'evolve' in available and (enemy or can_face):
            return 'evolve'
        return None

    # 3. Voralai (10644120): Super Evolve gives 3x Abyss (3 burn + 3 heal), Evolve gives 1x Abyss
    if cid == VORALAI:
        if 'super_evolve' in available and (len(hand) <= 6) and not hold_sep_for_finisher:
            return 'super_evolve'
        if 'evolve' in available and (len(hand) <= 8):
            return 'evolve'

    # 4. Outlaw (10943110): Evolve summons Storm Raider (10942110)
    if cid == OUTLAW and len(state.get('own') or []) < 5:
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and not hold_sep_for_finisher:
            return 'super_evolve'

    # 5. Carrier Dragon (10741120): Evolve buffs a follower in hand +2/+2
    if cid == CARRIER and any(c.get('id') in FOLLOWER_IDS for c in hand):
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and not hold_sep_for_finisher:
            return 'super_evolve'

    # 6. Chimica (10842120): Evolve discards 1, draws 1, heals 1
    if cid == CHIMICA and len(hand) >= 1 and (has_good_discard_fodder(hand, state) or len(hand) >= 2):
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and has_good_discard_fodder(hand, state) and not hold_sep_for_finisher:
            return 'super_evolve'

    # 7. High-impact Storm finishers (Kiritsu / Antimaria / Raider / Scout) hitting face
    if can_face and cid in (KIRITSU, ANTIMARIA, RAIDER, SCOUT, BANDIT, FIRE_DRAKE):
        if 'super_evolve' in available and (cid in (KIRITSU, ANTIMARIA) or 'evolve' not in available):
            return 'super_evolve'
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and face_dmg >= 2:
            return 'super_evolve'

    # 7b. General face pressure & tempo evolution (don't hold EP/SEP to the grave!)
    turn = state.get('turn', 0)
    is_ready_attacker = can_face or bool(unit.get('can_face')) or bool(unit.get('ready') and not enemy)
    if is_ready_attacker:
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and (turn >= 6 or (enemy_hp is not None and enemy_hp <= 15)):
            return 'super_evolve'

    # 8. Evolve to rush-clear a dangerous enemy follower or ward
    if enemy and (not unit.get('ready') or not unit.get('can_face')):
        if 'evolve' in available:
            return 'evolve'
        if 'super_evolve' in available and (not hold_sep_for_finisher or any(e.get('ward') or (e.get('attack') or 0) >= 4 for e in enemy)):
            return 'super_evolve'

    return None


def ordered_cards(hand, pp, max_pp, board_count, state=None, settings=None):
    """Return playable cards in optimal execution order for the current step."""
    if pp is None:
        return []
    state = state or {}
    enemy = state.get('enemy') or []
    own = state.get('own') or []
    enemy_hp = state.get('enemy_hp')
    combo_active = bool(state.get('attacked_leader_last_turn'))
    wards = [e for e in enemy if e.get('ward') and e.get('attackable', True)]
    legal_enemy_targets = [e for e in enemy if not e.get('aura')]
    has_ready_face_unit = any(can_unit_hit_face(u, state) for u in own)

    # Lethal turn awareness: calculate board + evo + hand burst potential
    projected_turn_dmg = estimate_turn_face_damage(state, pp_override=pp)
    is_lethal_turn = bool(enemy_hp is not None and projected_turn_dmg >= enemy_hp)
    evo_face_bonus = 3 if state.get('super_available') else (2 if state.get('ep_available') else 0)
    board_plus_evo_face = sum(attacker_face_damage(u, state) for u in own if can_unit_hit_face(u, state, ignore_wards=False)) + evo_face_bonus
    pierce_plus_evo_face = sum(attacker_face_damage(u, state) for u in own if can_unit_hit_face(u, state, ignore_wards=True)) + evo_face_bonus

    # Check if we have non-Tantrum cards that can spend PP first so Tantrum (10941310)
    # doesn't accidentally consume 3 PP via Enhance_3 when we want to play a 2PP/3PP card + 1PP Tantrum!
    other_playable_exists = False
    if pp >= 3:
        for c in hand:
            cid = c.get('id')
            cost = c.get('cost')
            if cid == TANTRUM or cid == 'unknown' or cost is None or cost > pp:
                continue
            if cid in FOLLOWER_IDS and board_count >= 5:
                continue
            if cid == RED_TORRENT and (not legal_enemy_targets or len(hand) < 2):
                continue
            if cid == FANGS and not enemy:
                continue
            if cid == BLADE_GRANT and not own:
                continue
            if cid == VORALAI and any(x.get('id') in DISCARD_PLAY_SOURCES and (x.get('cost') or 99) <= pp for x in hand):
                continue
            if not enemy or (pp - cost in (1, 2)):
                other_playable_exists = True
                break

    out = []
    for idx, c in enumerate(hand):
        cid = c['id']
        if cid == 'unknown' or c.get('cost') is None:
            continue
        eff_cost = effective_card_cost(c, pp)
        if eff_cost is None or eff_cost > pp:
            continue
        if cid in FOLLOWER_IDS and board_count >= 5:
            continue

        # Card-specific legality and tactical guards
        if cid == RED_TORRENT:
            if len(hand) < 2 or not legal_enemy_targets:
                continue
        if cid == FANGS and not enemy:
            continue
        if cid == BLADE_GRANT:
            if not own:
                continue
            if eff_cost >= 4 and any(
                x.get('id') in DISCARD_PLAY_SOURCES and i != idx and (x.get('cost') or 99) <= pp
                for i, x in enumerate(hand)
            ):
                continue
        if cid == VORALAI:
            if any(
                x.get('id') in DISCARD_PLAY_SOURCES and i != idx and (x.get('cost') or 99) <= pp
                for i, x in enumerate(hand)
            ):
                continue
        if cid == ABYSS:
            is_abyss_lethal = enemy_hp is not None and (enemy_hp <= 1 or is_lethal_turn)
            if not is_abyss_lethal and any(
                x.get('id') in DISCARD_PLAY_SOURCES and i != idx and (x.get('cost') or 99) <= pp
                for i, x in enumerate(hand)
            ):
                continue
        if cid == BANDIT and not combo_active:
            # Conserve 1PP Bandit in mid/late game when we already hit (or will hit) leader this turn
            # so Bandit becomes a 1PP Storm attacker next turn (or discard fodder), unless board is empty or Carrier/Blade Grant needs a body
            will_activate_combo_next = bool(has_ready_face_unit or state.get('attacked_leader_this_turn'))
            needs_body = not own or any(x.get('id') in (CARRIER, BLADE_GRANT) and (x.get('cost') or 99) <= pp - 1 for x in hand)
            if (max_pp or pp) >= 3 and will_activate_combo_next and not needs_body and len(hand) <= 7:
                continue

        # Scoring & priority ordering
        score = 50
        tier = 'tempo'
        form = 'enhance' if (cid == TANTRUM and pp >= 3) else 'base'

        # 1. Immediate lethal finishers & burn
        if cid == ABYSS and enemy_hp is not None and (enemy_hp <= 1 or is_lethal_turn):
            score, tier = 200, 'finish'
        elif cid == ANTIMARIA and combo_active:
            # 6/5 Storm that ignores Ward! Prioritize as lethal finisher whenever board+evo+6 >= enemy_hp or turn is lethal!
            is_antimaria_lethal = enemy_hp is not None and (enemy_hp <= 9 or pierce_plus_evo_face + 6 >= enemy_hp or is_lethal_turn)
            score, tier = (198 if is_antimaria_lethal else 148), 'finish'
        elif cid == KIRITSU:
            # 5/4 Storm + Bane + Aura, discards 1 and adds 2 Red Torrents
            is_kiritsu_lethal = not wards and enemy_hp is not None and (enemy_hp <= 8 or board_plus_evo_face + 5 >= enemy_hp or is_lethal_turn)
            score, tier = (194 if is_kiritsu_lethal else 142), 'finish'
        elif cid == RED_TORRENT and wards and (has_ready_face_unit or pp >= 2):
            # Clear enemy Ward for 1 PP so our attackers can go face!
            score, tier = (196 if is_lethal_turn else 175), 'clear_ward'
        elif cid == BLADE_GRANT and eff_cost <= 2 and has_ready_face_unit:
            # 2PP +2/+2 on a ready face attacker
            score, tier = (195 if (is_lethal_turn and not wards) else 165), ('finish' if (is_lethal_turn and not wards) else 'buff_face')
        elif cid == CARRIER and own:
            # 4PP 3/2 that gives +2/+2 to an allied follower already on board
            score, tier = (190 if (is_lethal_turn and has_ready_face_unit and not wards) else 160 if has_ready_face_unit else 135), ('finish' if (is_lethal_turn and has_ready_face_unit and not wards) else 'buff_board')
        elif cid == RAIDER and is_lethal_turn and not wards:
            score, tier = 192, 'finish'
        elif cid == BANDIT and combo_active and is_lethal_turn and not wards:
            score, tier = 191, 'finish'
        elif cid == OUTLAW and enemy:
            # 5PP 4/4 dealing 4 (or 8) damage to enemy board + Evo summons Storm Raider
            score, tier = (158 if combo_active else 142), 'tempo_swing'
        elif cid == GILVA and enemy and (enemy_hp is not None and enemy_hp < 20):
            # 6PP 4/4 distributing (20 - enemy_hp) AOE damage across enemy board
            aoe_dmg = max(0, 20 - enemy_hp)
            enemy_total_hp = sum(e.get('health') or 0 for e in enemy)
            score, tier = (156 if aoe_dmg >= min(4, enemy_total_hp) else 125), 'aoe_swing'
        elif cid == FANGS and enemy:
            # 2PP 3 (or 6) distributed damage
            score, tier = (152 if (combo_active and len(enemy) >= 1) else 128), 'aoe_clear'
        elif cid == SCOUT:
            # 3PP 1/3 Stealth - sets up guaranteed leader hit next turn + free Evolution!
            score, tier = 138, 'stealth_setup'
        elif cid == CHIMICA and has_good_discard_fodder(hand, state, exclude_index=idx):
            # 2PP 2/1 discarding Voralai / 4c Blade Grant / Abyss!
            score, tier = 148, 'discard_combo'
        elif cid == DRAGOON and has_good_discard_fodder(hand, state, exclude_index=idx):
            # 3PP 2/3 discarding Voralai / 4c Blade Grant / Abyss!
            score, tier = 144, 'discard_combo'
        elif cid == RAIDER:
            # 2PP 1/2 Storm (2/2 on strike if combo active)
            score, tier = (139 if (combo_active or not own) else 126), 'storm_tempo'
        elif cid == BANDIT and combo_active:
            # 1PP 1/1 with Storm active!
            score, tier = 136, 'storm_tempo'
        elif cid == TANTRUM:
            if form == 'base':
                # 1PP 1/1 Intimidate Fire Drake!
                score = 134 if pp == 1 else 122
            else:
                # Enhance 3: if we have another playable card that wants PP first, defer Tantrum until PP drops to 1-2!
                if other_playable_exists:
                    score = 40
                elif enemy:
                    score = 130
                else:
                    score = 85
        elif cid == CHIMICA:
            score = 118 if len(hand) >= 2 else 95
        elif cid == DRAGOON:
            score = 120 if len(hand) >= 2 else 98
        elif cid == OUTLAW:
            score = 115
        elif cid == GILVA:
            score = 132 if state.get('super_available') else 110
        elif cid == BANDIT:
            # 1PP 1/1 without Storm: play on curve if no other 1PP play, or to provide a body for Carrier / Blade Grant
            score = 112 if (pp == 1 or not own) else 65
        elif cid == CARRIER and not own:
            # Playing Carrier on an empty board wastes its +2/+2 Fanfare; only play if we have no other follower first
            has_cheaper_follower = any(
                x.get('id') in FOLLOWER_IDS and i != idx and effective_card_cost(x, pp) is not None and effective_card_cost(x, pp) <= pp - 4
                for i, x in enumerate(hand)
            )
            score = 45 if has_cheaper_follower else 92
        elif cid == BLADE_GRANT:
            score = 116 if has_ready_face_unit else 96
        elif cid == ABYSS:
            score = 105
        elif cid == RED_TORRENT:
            score = 124 if any((e.get('attack') or 0) >= 3 or e.get('ward') for e in legal_enemy_targets) else 80
        elif cid == ANTIMARIA:
            score = 125 if enemy else 90

        out.append({
            **c,
            'cost': eff_cost,
            'form': form,
            'policy_tier': tier,
            'priority_score': score,
        })

    out.sort(key=lambda x: (-x['priority_score'], x['cost'], x['xy'][0]))
    return out


def plan_attacks(state, attacked):
    """Plan the next single follower attack on our turn."""
    enemy = [e for e in (state.get('enemy') or []) if e.get('attackable', True)]
    blocked = _blocked_set(state)
    ready = [
        u for u in (state.get('own') or [])
        if (u.get('ready') or u.get('can_face') or state.get('force_ready_face_this_turn'))
        and u.get('uid') not in attacked
        and (u.get('attack') is None or u.get('attack') > 0)
    ]
    if not ready:
        return None

    wards = [e for e in enemy if e.get('ward')]
    enemy_hp = state.get('enemy_hp')
    own_hp = state.get('own_hp')
    leader_hit_done = bool(state.get('attacked_leader_this_turn'))
    unevolved_other_exists = lambda uid: any(
        u.get('uid') != uid and not u.get('evolved') and not u.get('super') and not u.get('no_evolve')
        for u in (state.get('own') or [])
    )

    # Calculate projected total face damage from all ready face-capable followers
    force_face = bool(state.get('force_ready_face_this_turn'))
    face_units = [
        u for u in ready
        if (u.get('can_face') or force_face) and (not wards or u.get('ignore_ward') or u.get('card_id') == ANTIMARIA)
        and (u.get('uid'), tuple(FACE)) not in blocked
    ]
    projected_face = sum(attacker_face_damage(u, state) for u in face_units)
    # Add +2 if Scout is among face_units and can free-evolve another ready face follower!
    if any(u.get('card_id') == SCOUT for u in face_units) and any(
        u.get('card_id') != SCOUT and not u.get('evolved') and not u.get('super') for u in face_units
    ):
        projected_face += 2

    def attacker_order_key(u):
        is_scout_with_target = (u.get('card_id') == SCOUT and unevolved_other_exists(u.get('uid')))
        return (
            1 if is_scout_with_target else 0,
            1 if (u.get('ignore_ward') or u.get('card_id') == ANTIMARIA) else 0,
            attacker_face_damage(u, state),
            -u['xy'][0],
        )

    # 1. Immediate lethal on enemy leader: go straight for face!
    if face_units and enemy_hp is not None and projected_face >= enemy_hp:
        chosen = max(face_units, key=attacker_order_key)
        return {
            'source': chosen,
            'target': FACE,
            'reason': 'lethal_face',
            'projected_face_damage': projected_face,
        }

    # 2. If enemy has Wards, non-piercing followers MUST clear Wards first
    if wards:
        piercers = [u for u in face_units if u.get('ignore_ward') or u.get('card_id') == ANTIMARIA]
        if piercers and not leader_hit_done:
            chosen = max(piercers, key=attacker_order_key)
            return {
                'source': chosen,
                'target': FACE,
                'reason': 'pierce_ward_keep_combo',
                'projected_face_damage': projected_face,
            }

        best_ward_trade = None
        for u in ready:
            u_atk = attacker_strike_attack(u, state)
            u_hp = u.get('health') or 1
            for w in wards:
                if (u.get('uid'), tuple(w['xy'])) in blocked:
                    continue
                w_atk = w.get('attack') or 0
                w_hp = w.get('health') or 1
                kills = bool(u.get('bane') or u_atk >= w_hp)
                survives = bool(u_hp > w_atk and not w.get('bane'))
                is_scout_trigger = (u.get('card_id') == SCOUT and unevolved_other_exists(u.get('uid')))
                score = (
                    1 if kills else 0,
                    1 if is_scout_trigger else 0,
                    1 if (u.get('bane') and w_hp >= 4) else 0,
                    1 if not u.get('can_face') else 0,
                    1 if survives else 0,
                    w_hp if kills else u_atk,
                    -(u_atk if kills else 0),
                )
                if best_ward_trade is None or score > best_ward_trade[0]:
                    best_ward_trade = (score, u, w)
        if best_ward_trade:
            _, u, w = best_ward_trade
            return {
                'source': u,
                'target': w['xy'],
                'reason': 'clear_ward',
                'projected_face_damage': 0,
            }
        if piercers:
            chosen = max(piercers, key=attacker_order_key)
            return {
                'source': chosen,
                'target': FACE,
                'reason': 'pierce_ward_face',
                'projected_face_damage': projected_face,
            }
        return None

    # 3. No enemy Wards:
    # Rule A: Rush-only followers (cannot hit face) must trade into enemy followers if beneficial
    rush_only = [u for u in ready if not u.get('can_face')]
    if rush_only and enemy:
        best_rush = None
        for u in rush_only:
            u_atk = attacker_strike_attack(u, state)
            u_hp = u.get('health') or 1
            for e in enemy:
                if (u.get('uid'), tuple(e['xy'])) in blocked:
                    continue
                e_atk = e.get('attack') or 0
                e_hp = e.get('health') or 1
                kills = bool(u.get('bane') or u_atk >= e_hp)
                survives = bool(u_hp > e_atk and not e.get('bane'))
                score = (
                    1 if kills else 0,
                    1 if (u.get('bane') and (e_atk + e_hp >= 6)) else 0,
                    e_atk * 2 + e_hp,
                    1 if survives else 0,
                    -u_atk,
                )
                if best_rush is None or score > best_rush[0]:
                    best_rush = (score, u, e)
        if best_rush:
            _, u, e = best_rush
            return {
                'source': u,
                'target': e['xy'],
                'reason': 'rush_follower_trade',
                'projected_face_damage': projected_face,
            }

    # Rule B: Ensure we hit enemy leader AT LEAST ONCE this turn to activate next turn's combo
    # (unless enemy board has lethal on us AND we must trade with all attackers to survive)
    enemy_board_atk = sum(e.get('attack') or 0 for e in enemy)
    must_defend_lethal = bool(own_hp is not None and enemy_board_atk >= own_hp and enemy)

    if face_units and not leader_hit_done and not must_defend_lethal:
        chosen = max(face_units, key=attacker_order_key)
        return {
            'source': chosen,
            'target': FACE,
            'reason': 'establish_leader_hit_combo',
            'projected_face_damage': projected_face,
        }

    # Rule C: High-value protective / Bane / free-kill trades if enemy threatens lethal or huge value
    if enemy:
        for u in ready:
            u_atk = attacker_strike_attack(u, state)
            u_hp = u.get('health') or 1
            for e in sorted(enemy, key=lambda x: -((x.get('attack') or 0) * 2 + (x.get('health') or 0))):
                if (u.get('uid'), tuple(e['xy'])) in blocked:
                    continue
                e_atk = e.get('attack') or 0
                e_hp = e.get('health') or 1
                kills = bool(u.get('bane') or u_atk >= e_hp)
                survives = bool(u_hp > e_atk and not e.get('bane'))
                if kills and (must_defend_lethal or (survives and e_atk >= 4 and u_atk <= 3) or (u.get('bane') and u_atk <= 2 and e_atk >= 4)):
                    return {
                        'source': u,
                        'target': e['xy'],
                        'reason': 'defensive_or_value_trade',
                        'projected_face_damage': projected_face,
                    }

    # Rule D: Send all remaining face-capable attackers to the enemy leader!
    if face_units:
        chosen = max(face_units, key=attacker_order_key)
        return {
            'source': chosen,
            'target': FACE,
            'reason': 'tempo_face_pressure',
            'projected_face_damage': projected_face,
        }

    return None
