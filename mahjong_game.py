# mahjong_game.py (移除了混儿牌和过水不胡逻辑)
# 定义麻将游戏的核心逻辑，包括牌堆、玩家、游戏流程等

import random
from collections import Counter
import copy
import logging
from mahjong_common import (
    ALL_TILES_SUIT, ALL_TILES_WIND, ALL_TILES_DRAGON,
    TILES_PER_TYPE, INITIAL_HAND_SIZE, sort_tiles, tile_sort_key,
    is_triplet, is_quad, is_pair,
)

logger = logging.getLogger(__name__)


class Player:
    """表示一个玩家及其状态和操作。"""

    def __init__(self, player_id, name):
        self.player_id = player_id
        self.name = name
        self.hand = []
        self.melds = []
        self.discarded = []

        self.is_listening = False
        self.listening_tiles = []
        self.fixed_listening_tiles = []
        self.is_attempting_ting = False
        self.current_drawn_tile_for_auto_discard = None

        self.can_hu_zimo = False
        self.can_pong = False
        self.can_gang = False  # 响应他人弃牌的明杠
        self.can_hu_discard = False
        self.possible_an_gangs = []
        self.possible_bu_gangs = []

    def add_tile(self, tile):
        self.hand.append(tile)
        self.hand = sort_tiles(self.hand)

    def remove_tile(self, tile):
        try:
            self.hand.remove(tile)
            self.hand = sort_tiles(self.hand)
            return True
        except ValueError:
            logger.warning(f"玩家 {self.name} ({self.player_id}) 尝试移除不存在的牌: {tile} 从手牌 {self.hand}")
            return False

    def can_pong_tile(self, tile_to_check, game_rules=None):  # game_rules 参数保留但未使用（除非未来添加其他规则）
        if self.is_listening:
            return False
        return self.hand.count(tile_to_check) >= 2

    def perform_pong(self, tile_to_pong):
        if self.hand.count(tile_to_pong) < 2:
            return False
        self.remove_tile(tile_to_pong)
        self.remove_tile(tile_to_pong)
        self.melds.append(sort_tiles([tile_to_pong, tile_to_pong, tile_to_pong]))
        self.melds = sorted(self.melds, key=lambda m: tile_sort_key(m[0]))
        return True

    def find_possible_gangs(self, tile_from_discard=None, game_rules=None, drawn_tile_in_turn=None):
        possible_an_gangs_val = []
        possible_bu_gangs_val = []
        possible_ming_gangs_val = []

        hand_counts = Counter(self.hand)

        # 1. 查找暗杠 (An Gang) - 手牌中有4张相同的牌
        for tile_val, count in hand_counts.items():
            if count == 4:
                possible_an_gangs_val.append(tile_val)

        # 2. 查找补杠 (Bu Gang / Additive Kong) - 手牌中有一张与已碰出的刻子相同的牌
        for i, meld in enumerate(self.melds):
            if is_triplet(meld):
                tile_in_meld = meld[0]
                if hand_counts.get(tile_in_meld, 0) >= 1:
                    possible_bu_gangs_val.append((i, tile_in_meld))

        # 3. 查找明杠 (Ming Gang) - 仅当 tile_from_discard 非空时，手牌中有3张与弃牌相同的牌
        if tile_from_discard and not self.is_listening:
            if hand_counts.get(tile_from_discard, 0) == 3:
                possible_ming_gangs_val.append(tile_from_discard)

        self.possible_an_gangs = list(set(possible_an_gangs_val))
        self.possible_bu_gangs = list(set(possible_bu_gangs_val))

        return self.possible_an_gangs, self.possible_bu_gangs, list(set(possible_ming_gangs_val))

    def perform_gang(self, gang_type, tile_info, tile_discarded_for_ming_gang=None, game_rules=None):
        original_hand = copy.deepcopy(self.hand)
        original_melds = copy.deepcopy(self.melds)

        try:
            if gang_type == "an":
                target_tile = tile_info
                if self.hand.count(target_tile) < 4:  # 暗杠必须手牌4张
                    logger.error(f"暗杠时手牌不足4张: {target_tile} (玩家 {self.name})")
                    return False
                for _ in range(4): self.remove_tile(target_tile)
                self.melds.append(sort_tiles([target_tile] * 4))
                logger.debug(f"{self.name} 执行暗杠: {target_tile}")

            elif gang_type == "bu":
                meld_index, tile_to_complete_meld = tile_info
                if not (0 <= meld_index < len(self.melds) and
                        is_triplet(self.melds[meld_index]) and
                        self.melds[meld_index][0] == tile_to_complete_meld):
                    return False
                if self.hand.count(tile_to_complete_meld) < 1:
                    return False
                self.remove_tile(tile_to_complete_meld)
                self.melds[meld_index].append(tile_to_complete_meld)
                self.melds[meld_index] = sort_tiles(self.melds[meld_index])
                logger.debug(f"{self.name} 执行补杠: {tile_to_complete_meld}")

            elif gang_type == "ming":
                target_tile = tile_info
                if self.hand.count(target_tile) < 3:  # 明杠需要手牌3张
                    return False
                for _ in range(3): self.remove_tile(target_tile)
                self.melds.append(sort_tiles([target_tile] * 4))
                logger.debug(f"{self.name} 执行明杠: {target_tile} (杠的是 {tile_discarded_for_ming_gang})")
            else:
                return False

            self.melds = sorted(self.melds, key=lambda m: tile_sort_key(m[0]))
            return True
        except Exception as e:
            logger.exception(f"执行杠操作时发生错误 (玩家 {self.name}, 类型 {gang_type}, 信息 {tile_info})")
            self.hand = original_hand
            self.melds = original_melds
            return False

    def get_tile_type_and_value(self, tile_str):  # 保持不变
        if tile_str is None: return None, None
        parts = tile_str.split('_')
        suit = parts[0]
        value_str = parts[1]
        if suit in ["wan", "tiao", "tong"]:
            try:
                return suit, int(value_str)
            except ValueError:
                return suit, value_str
        return suit, value_str

    def _can_form_melds_recursive(self, current_tiles_list, game_rules):  # 移除 num_jokers
        if not current_tiles_list:
            return True

        sorted_current_tiles = sort_tiles(current_tiles_list)
        counts = Counter(sorted_current_tiles)
        first_tile = sorted_current_tiles[0]

        # 1. 尝试移除刻子 (AAA)
        if counts.get(first_tile, 0) >= 3:
            remaining_after_triplet = list(sorted_current_tiles)
            for _ in range(3): remaining_after_triplet.remove(first_tile)
            if self._can_form_melds_recursive(remaining_after_triplet, game_rules):
                return True

        # 2. 尝试移除顺子 (ABC)
        tile_type, tile_value = self.get_tile_type_and_value(first_tile)
        if tile_type in ["wan", "tiao", "tong"] and isinstance(tile_value, int) and tile_value <= 7:
            t1_str, t2_str, t3_str = first_tile, f"{tile_type}_{tile_value + 1}", f"{tile_type}_{tile_value + 2}"
            if counts.get(t1_str, 0) >= 1 and counts.get(t2_str, 0) >= 1 and counts.get(t3_str, 0) >= 1:
                temp_list = list(sorted_current_tiles)
                temp_list.remove(t1_str);
                temp_list.remove(t2_str);
                temp_list.remove(t3_str)
                if self._can_form_melds_recursive(temp_list, game_rules):
                    return True
        return False

    def check_standard_win(self, tiles_for_check, game_rules):  # 移除 joker 相关
        if len(tiles_for_check) % 3 != 2 or len(tiles_for_check) < 2:
            return False

        non_joker_tiles = list(tiles_for_check)  # 现在所有牌都是非混儿牌
        unique_tiles_for_pair = sort_tiles(list(set(non_joker_tiles)))

        for pair_tile in unique_tiles_for_pair:
            if non_joker_tiles.count(pair_tile) >= 2:
                remaining_tiles = list(non_joker_tiles)
                remaining_tiles.remove(pair_tile);
                remaining_tiles.remove(pair_tile)
                if self._can_form_melds_recursive(remaining_tiles, game_rules):
                    return True
        return False

    def can_hu_tile(self, tile_to_win=None, is_zimo=False, game_rules=None, hand_override=None):
        current_hand = hand_override if hand_override is not None else self.hand
        all_tiles_for_check = []
        all_tiles_for_check.extend(current_hand)
        for meld_group in self.melds:
            all_tiles_for_check.extend(meld_group)

        if is_zimo:
            if tile_to_win and tile_to_win not in current_hand:
                pass
        elif tile_to_win:
            all_tiles_for_check.append(tile_to_win)

        all_tiles_for_check = sort_tiles(all_tiles_for_check)

        # 1. 检查标准胡牌 (m * 面子 + 1 * 将)
        if len(all_tiles_for_check) % 3 == 2 and self.check_standard_win(all_tiles_for_check, game_rules):
            logger.debug(f"标准胡牌结构检查通过 (check_standard_win): {all_tiles_for_check}")
            return True

        # 2. 检查七对 (14张牌, 没有亮牌, 7个对子)
        if len(all_tiles_for_check) == 14 and not self.melds:
            counts_qidu = Counter(all_tiles_for_check)
            pairs_found = 0
            for count in counts_qidu.values():
                if count == 2:
                    pairs_found += 1
                elif count == 4:
                    pairs_found += 2  # 四张算两对 (豪华七对基础)

            if pairs_found == 7:
                logger.debug(f"七对检查通过: {all_tiles_for_check}")
                return True
        return False

    def find_listening_tiles(self, game_rules, possible_draw_tiles_list=None, hand_to_check=None):
        current_hand = hand_to_check if hand_to_check is not None else self.hand

        if len(current_hand) % 3 != 1:
            return []

        if possible_draw_tiles_list is None:
            all_game_tiles_unique = list(set(ALL_TILES_SUIT + ALL_TILES_WIND + ALL_TILES_DRAGON))
        else:
            all_game_tiles_unique = list(set(possible_draw_tiles_list))

        listening = set()
        for test_tile in all_game_tiles_unique:
            if self.can_hu_tile(tile_to_win=test_tile, is_zimo=False, game_rules=game_rules,
                                hand_override=current_hand):
                listening.add(test_tile)

        result_listening_tiles = sort_tiles(list(listening))
        if hand_to_check is None:  # 更新自身听牌列表当检查自身手牌时
            self.listening_tiles = result_listening_tiles
            logger.debug(
                f"玩家 {self.name} 计算听牌结果 (手牌 {self.hand}, 亮牌 {self.melds}): {self.listening_tiles}")
        return result_listening_tiles


class GameRules:
    """存储游戏特定规则的配置类。"""

    def __init__(self, include_winds_dragons=True):  # 简化
        self.include_winds_dragons = include_winds_dragons
        logger.info(f"游戏规则初始化: 含风箭={include_winds_dragons}")


class Deck:
    """表示牌堆及其操作。"""

    def __init__(self, game_rules):
        self.tiles = []
        self.game_rules = game_rules  # GameRules 仍然有用，比如是否包含风牌箭牌
        for tile_str in ALL_TILES_SUIT: self.tiles.extend([tile_str] * TILES_PER_TYPE)
        if self.game_rules.include_winds_dragons:
            for tile_str in ALL_TILES_WIND: self.tiles.extend([tile_str] * TILES_PER_TYPE)
            for tile_str in ALL_TILES_DRAGON: self.tiles.extend([tile_str] * TILES_PER_TYPE)

        random.shuffle(self.tiles)
        self.initial_size = len(self.tiles)
        logger.debug(f"牌堆初始化完成，总共 {self.initial_size} 张牌。")

    def draw_tile(self):
        if self.tiles: return self.tiles.pop(0)
        return None

    def draw_from_end(self):
        if self.tiles: return self.tiles.pop()
        return None

    def remaining(self):
        return len(self.tiles)


class Game:  # Game 类中的大部分逻辑保持，但其调用的 Player 方法已简化
    """游戏主类，管理游戏流程、状态和玩家交互。"""

    def __init__(self, num_players=4, game_rules_config=None):
        if not 2 <= num_players <= 4:
            raise ValueError("玩家人数必须在2到4之间")
        self.num_players = num_players
        # 初始化游戏规则 (game_rules_config 现在只关心 include_winds_dragons)
        rules_config_simplified = {}
        if game_rules_config and "include_winds_dragons" in game_rules_config:
            rules_config_simplified["include_winds_dragons"] = game_rules_config["include_winds_dragons"]
        self.game_rules = GameRules(**rules_config_simplified)

        self.deck = None
        self.players = []
        self.current_turn = 0
        self.discard_pile = []
        self.last_discarded_tile = None
        self.last_discarder_id = None
        self.game_state = "waiting"
        self.winning_player_id = None
        self.winning_tile = None
        self.action_pending = False
        self.action_responses = {}
        self._pending_action_info = None
        self._next_prompt_info = None

        # 生成所有游戏中会用到的牌的列表（用于听牌检查等）
        temp_all_tiles = list(ALL_TILES_SUIT)
        if self.game_rules.include_winds_dragons:
            temp_all_tiles.extend(ALL_TILES_WIND)
            temp_all_tiles.extend(ALL_TILES_DRAGON)
        self.all_game_tiles_list = list(set(temp_all_tiles))

        logger.info(f"游戏实例初始化: {num_players}人。规则见 GameRules 日志。")

    def add_player(self, player_obj):  # 保持不变
        if self.game_state != "waiting":
            return False
        if len(self.players) < self.num_players:
            self.players.append(player_obj)
            return True
        return False

    def start_game(self):
        if len(self.players) != self.num_players:
            logger.error(f"玩家数量不足 ({len(self.players)}/{self.num_players})，无法开始游戏。")
            return False

        logger.info("游戏开始发牌...")
        self.game_state = "dealing"
        self.deck = Deck(self.game_rules)

        for p in self.players:
            p.hand = []
            p.melds = []
            p.discarded = []
            p.is_listening = False
            p.listening_tiles = []
            p.fixed_listening_tiles = []
            p.is_attempting_ting = False
            p.current_drawn_tile_for_auto_discard = None

        for _ in range(INITIAL_HAND_SIZE):  # 发13张牌
            for player in self.players:
                tile = self.deck.draw_tile()
                if tile:
                    player.add_tile(tile)
                else:
                    self.end_game("发牌时牌不够"); return False

        self.current_turn = 0
        dealer = self.players[self.current_turn]
        logger.info(f"发牌完成。庄家是 {dealer.name} ({dealer.player_id})。")

        self.game_state = "playing"
        # 庄家开始他的第一个回合，正常摸一张牌 (第14张)
        if not self._start_player_turn_logic(self.current_turn):  # 直接调用，不需要 is_initial_deal_draw
            logger.error("开始庄家回合失败。")
            return False
        return True

    def get_player_by_id(self, player_id):  # 保持不变
        for player in self.players:
            if player.player_id == player_id: return player
        return None

    def get_player_index_by_id(self, player_id):  # 保持不变
        for i, player in enumerate(self.players):
            if player.player_id == player_id: return i
        return -1

    def get_state_for_player(self, player_id_to_get_state_for):  # 移除 joker_tile
        player_obj = self.get_player_by_id(player_id_to_get_state_for)
        if not player_obj: return None

        state = {
            "game_state": self.game_state,
            "current_turn_player_id": self.players[
                self.current_turn].player_id if self.game_state == "playing" and self.players else None,
            "players": [
                {
                    "player_id": p.player_id, "name": p.name,
                    "is_current_turn": (self.game_state == "playing" and p.player_id == self.players[
                        self.current_turn].player_id),
                    "hand_size": len(p.hand), "melds": p.melds, "discarded": p.discarded,
                    "is_listening": p.is_listening,
                    "listening_tiles": p.listening_tiles if p.player_id == player_id_to_get_state_for and p.is_listening else [],
                } for p in self.players
            ],
            "your_hand": player_obj.hand,
            "last_discarded_tile": self.last_discarded_tile,
            "last_discarder_id": self.last_discarder_id,
            "wall_remaining": self.deck.remaining() if self.deck else 0,
            "winning_player_id": self.winning_player_id,
            "winning_tile": self.winning_tile,
            "action_pending": self.action_pending,
            "pending_action_info": self._pending_action_info if self.action_pending else None,
            "is_attempting_ting": player_obj.is_attempting_ting
        }
        return state

    def _start_player_turn_logic(self, player_index, drawn_tile_override=None, is_gang_replacement_draw=False):
        if self.game_state != "playing": return False
        if not (0 <= player_index < len(self.players)): return False

        player = self.players[player_index]
        self.current_turn = player_index

        # 移除过水相关的重置:
        # if self.game_rules.enable_passed_hu_rule: player.passed_hu_on_zimo_opportunity = False

        logger.info(f"--- 轮到 {player.name} ({player.player_id}) 回合 ---")

        drawn_tile_this_turn = None
        if drawn_tile_override:
            drawn_tile_this_turn = drawn_tile_override
        else:  # 正常摸牌
            drawn_tile_this_turn = self.deck.draw_tile()
            if not drawn_tile_this_turn:
                self.end_game("牌摸完了 (流局)")
                return False
            player.add_tile(drawn_tile_this_turn)

        logger.debug(
            f"{player.name} {'摸到' if not drawn_tile_override else ('杠后补到' if is_gang_replacement_draw else '处理')}一张牌: {drawn_tile_this_turn} (手牌: {player.hand})")
        player.current_drawn_tile_for_auto_discard = drawn_tile_this_turn

        actions = []
        # drawn_tile_in_turn 对 find_possible_gangs 不再那么重要，因为它现在只看手牌
        possible_an, possible_bu, _ = player.find_possible_gangs(game_rules=self.game_rules)

        player.can_hu_zimo = player.can_hu_tile(tile_to_win=drawn_tile_this_turn, is_zimo=True,
                                                game_rules=self.game_rules)
        if player.can_hu_zimo:
            actions.append("hu")

        if player.is_listening:
            allowed_gangs_after_ting = []
            # 检查暗杠是否改变听牌
            if possible_an:
                for an_tile in possible_an:
                    if self._check_gang_maintains_listen(player, "an", an_tile, drawn_tile_this_turn):
                        allowed_gangs_after_ting.append(("an", an_tile))
            # 检查补杠是否改变听牌
            if possible_bu:
                for bu_info in possible_bu:
                    if self._check_gang_maintains_listen(player, "bu", bu_info, drawn_tile_this_turn):
                        allowed_gangs_after_ting.append(("bu", bu_info))

            player.possible_an_gangs = [g[1] for g in allowed_gangs_after_ting if g[0] == 'an']
            player.possible_bu_gangs = [g[1] for g in allowed_gangs_after_ting if g[0] == 'bu']

            if allowed_gangs_after_ting:
                actions.append("gang")

            if "hu" not in actions and not allowed_gangs_after_ting:
                actions.append("discard")
            elif "hu" not in actions and allowed_gangs_after_ting:
                actions.append("discard")
            if "hu" in actions or allowed_gangs_after_ting:
                if "discard" not in actions: actions.append("discard")
        else:
            if possible_an or possible_bu: actions.append("gang")
            if not player.is_attempting_ting: actions.append("ting")
            actions.append("discard")

        if "discard" in actions: actions.remove("discard"); actions.append("discard")

        message = {
            "type": "action_prompt", "actions": list(set(actions)),
            "drawn_tile": drawn_tile_this_turn,
            "possible_an_gangs": player.possible_an_gangs,
            "possible_bu_gangs": player.possible_bu_gangs,
            "is_gang_replacement": is_gang_replacement_draw,
            "is_listening_player_turn": player.is_listening
        }
        self._next_prompt_info = (player.player_id, message)
        logger.debug(
            f"为玩家 {player.player_id} 设置行动提示: {actions}, 摸牌: {drawn_tile_this_turn}, 是否听牌回合: {player.is_listening}")
        return True

    def _check_gang_maintains_listen(self, player, gang_type, gang_info, drawn_tile_for_current_turn):
        if not player.is_listening or not player.fixed_listening_tiles:
            return False

        sim_player = Player(player.player_id, player.name)
        sim_player.hand = copy.deepcopy(player.hand)
        sim_player.melds = copy.deepcopy(player.melds)
        # sim_player.is_listening = True # 不再需要，find_listening_tiles 不依赖它
        # sim_player.fixed_listening_tiles = list(player.fixed_listening_tiles)

        if not sim_player.perform_gang(gang_type, gang_info, game_rules=self.game_rules):
            return False

            # 杠完后，手牌是10张 (或更少)，用这个手牌去计算新的听牌
        new_waits = sim_player.find_listening_tiles(game_rules=self.game_rules, hand_to_check=sim_player.hand)

        logger.debug(
            f"检查杠牌是否改变听牌: 原固定听牌 {player.fixed_listening_tiles}, 杠后 ({gang_type} {gang_info}) 新听牌 {new_waits}")
        return set(new_waits) == set(player.fixed_listening_tiles)

    def handle_player_action(self, player_id, action_data):  # 移除过水相关
        player_index = self.get_player_index_by_id(player_id)
        if player_index == -1 or player_index != self.current_turn:
            self.send_message_to_player(player_id, {"type": "error", "message": "不是你的回合"})
            return

        player = self.players[player_index]
        action_type = action_data.get("action_type")
        logger.info(f"玩家 {player.name} ({player_id}) 请求执行操作: {action_type} (数据: {action_data})")

        if action_type == "ting":
            if player.is_listening:
                self.send_message_to_player(player_id, {"type": "error", "message": "已叫听"})
                return
            if player.is_attempting_ting:
                self.send_message_to_player(player_id, {"type": "error", "message": "已在尝试听牌，请打牌"})
                return
            if len(player.hand) % 3 != 2:  # 摸牌后应为 3n+2
                self.send_message_to_player(player_id, {"type": "error", "message": "手牌数错误无法叫听"})
                return
            player.is_attempting_ting = True
            logger.info(f"玩家 {player.name} 声明尝试听牌。等待其打出一张牌以确认。")
            message = {
                "type": "action_prompt", "actions": ["discard"],
                "drawn_tile": player.current_drawn_tile_for_auto_discard,
                "is_listening_player_turn": False,
                "prompt_for_ting_discard": True
            }
            self._next_prompt_info = (player.player_id, message)
            return

        elif action_type == "discard":
            tile_to_discard = action_data.get("tile")
            if not tile_to_discard or tile_to_discard not in player.hand:
                self.send_message_to_player(player_id, {"type": "error", "message": "无效弃牌或牌不在手中"})
                self._start_player_turn_logic(self.current_turn,
                                              drawn_tile_override=player.current_drawn_tile_for_auto_discard)
                return

            if player.is_listening:
                if tile_to_discard != player.current_drawn_tile_for_auto_discard:
                    tile_to_discard = player.current_drawn_tile_for_auto_discard
                    if tile_to_discard is None or tile_to_discard not in player.hand:
                        self.end_game(f"玩家 {player.name} 状态异常导致游戏错误")
                        return

            player.remove_tile(tile_to_discard)
            # 移除过水相关:
            # if self.game_rules.enable_passed_hu_rule: ...

            self.discard_pile.append(tile_to_discard)
            player.discarded.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discarder_id = player_id
            logger.info(f"{player.name} 打出了 {tile_to_discard}")
            player.current_drawn_tile_for_auto_discard = None

            self.broadcast_message({"type": "player_discarded", "player_id": player_id, "tile": tile_to_discard})

            if player.is_attempting_ting:
                player.is_attempting_ting = False
                current_listens = player.find_listening_tiles(game_rules=self.game_rules, hand_to_check=player.hand)
                if current_listens:
                    player.is_listening = True
                    player.listening_tiles = list(current_listens)
                    player.fixed_listening_tiles = list(current_listens)
                    logger.info(
                        f"玩家 {player.name} 打出 {tile_to_discard} 后成功听牌，听: {player.fixed_listening_tiles}")
                    self.broadcast_message({"type": "player_tinged", "player_id": player_id,
                                            "listening_tiles": player.fixed_listening_tiles})
                else:
                    player.is_listening = False;
                    player.listening_tiles = [];
                    player.fixed_listening_tiles = []
                    logger.info(f"玩家 {player.name} 打出 {tile_to_discard} 后未能听牌。听牌尝试失败。")
                    self.send_message_to_player(player_id, {"type": "info", "message": "打牌后未能听牌，听牌取消。"})

            self.check_other_players_actions()
            return

        elif action_type == "hu":
            if player.can_hu_zimo:
                win_desc = "自摸"
                self.end_game(f"{player.name} {win_desc}胡了！", winner_id=player_id,
                              winning_tile=player.current_drawn_tile_for_auto_discard or win_desc)
                return
            else:
                self.send_message_to_player(player_id, {"type": "error", "message": "当前不能胡牌"})
                self._start_player_turn_logic(self.current_turn,
                                              drawn_tile_override=player.current_drawn_tile_for_auto_discard)
                return

        elif action_type == "gang":
            gang_type = action_data.get("gang_type")
            tile_info = action_data.get("tile_info")

            possible_an_for_player = player.possible_an_gangs
            possible_bu_for_player = player.possible_bu_gangs

            is_valid_gang_choice = False
            if gang_type == "an" and tile_info in possible_an_for_player:
                is_valid_gang_choice = True
            elif gang_type == "bu" and tile_info in possible_bu_for_player:
                is_valid_gang_choice = True

            if not is_valid_gang_choice:
                self.send_message_to_player(player_id, {"type": "error", "message": "无效的杠牌选择"})
                self._start_player_turn_logic(self.current_turn,
                                              drawn_tile_override=player.current_drawn_tile_for_auto_discard)
                return

            success = player.perform_gang(gang_type, tile_info, game_rules=self.game_rules)
            if success:
                g_tile_display = tile_info if gang_type == 'an' else tile_info[1]
                self.broadcast_message(
                    {"type": "player_ganged", "player_id": player.player_id, "tile": g_tile_display,
                     "gang_type": gang_type, "melds": player.melds})
                self._draw_and_handle_gang_replacement_logic(player)
                return
            else:
                self.send_message_to_player(player_id, {"type": "error", "message": "执行杠操作失败"})
                self._start_player_turn_logic(self.current_turn,
                                              drawn_tile_override=player.current_drawn_tile_for_auto_discard)
                return
        else:
            self.send_message_to_player(player_id, {"type": "error", "message": f"未知行动类型: {action_type}"})
            self._start_player_turn_logic(self.current_turn,
                                          drawn_tile_override=player.current_drawn_tile_for_auto_discard)
            return

    def _draw_and_handle_gang_replacement_logic(self, player):  # 保持大部分不变
        if self.game_state != "playing": return False
        replacement_tile = self.deck.draw_from_end()
        if not replacement_tile:
            self.end_game("杠后无牌可摸 (流局)")
            return False
        player.add_tile(replacement_tile)
        logger.debug(f"{player.name} 杠后补到: {replacement_tile} (手牌: {player.hand})")
        self._start_player_turn_logic(self.get_player_index_by_id(player.player_id),
                                      drawn_tile_override=replacement_tile,
                                      is_gang_replacement_draw=True)
        return True

    def check_other_players_actions(self):  # 移除过水相关
        discarded_tile = self.last_discarded_tile
        discarder_id = self.last_discarder_id
        if not discarded_tile or discarder_id is None: self._advance_turn_logic(); return

        possible_actions_for_players = {}
        self.action_responses = {}
        action_found_for_any_player = False
        discarder_index = self.get_player_index_by_id(discarder_id)
        if discarder_index == -1: self._advance_turn_logic(); return

        for i in range(1, self.num_players):
            player_index = (discarder_index + i) % self.num_players
            player = self.players[player_index]
            player_actions_available = []
            player.can_hu_discard = False;
            player.can_gang = False;
            player.can_pong = False

            if player.can_hu_tile(tile_to_win=discarded_tile, is_zimo=False, game_rules=self.game_rules):
                player_actions_available.append("hu");
                player.can_hu_discard = True

            if not player.is_listening:
                _, _, possible_ming = player.find_possible_gangs(tile_from_discard=discarded_tile,
                                                                 game_rules=self.game_rules)
                if possible_ming: player_actions_available.append("gang"); player.can_gang = True
                if player.can_pong_tile(discarded_tile, game_rules=self.game_rules):
                    player_actions_available.append("pong");
                    player.can_pong = True

            if player_actions_available:
                possible_actions_for_players[player.player_id] = player_actions_available
                self.action_responses[player.player_id] = None
                action_found_for_any_player = True

        if action_found_for_any_player:
            self.action_pending = True
            self._pending_action_info = {"type": "discard_response", "discarded_tile": discarded_tile,
                                         "discarder_id": discarder_id}
            for p_id, actions_list in possible_actions_for_players.items():
                final_actions_list = list(actions_list)
                if "pass" not in final_actions_list: final_actions_list.append("pass")
                message = {"type": "action_prompt", "actions": final_actions_list, "tile": discarded_tile,
                           "discarder_id": discarder_id, "is_response_prompt": True}
                self.send_message_to_player(p_id, message)
        else:
            self._reset_action_state_logic()
            self._advance_turn_logic()

    def handle_action_response(self, player_id, response_data):  # 移除过水相关
        if not self.action_pending or self._pending_action_info.get("type") != "discard_response": return
        if player_id not in self.action_responses or self.action_responses.get(player_id) is not None: return

        player = self.get_player_by_id(player_id)
        if not player: return

        response_type = response_data.get("action_type")
        discarded_tile_for_action = self._pending_action_info["discarded_tile"]
        allowed_server_side = ["pass"]
        if player.can_hu_discard: allowed_server_side.append("hu")
        if player.can_gang: allowed_server_side.append("gang")
        if player.can_pong: allowed_server_side.append("pong")

        if response_type not in allowed_server_side: response_type = "pass"

        # 移除过水相关:
        # if self.game_rules.enable_passed_hu_rule: ...

        self.action_responses[player_id] = response_type
        logger.info(f"玩家 {player.name} 响应对 {discarded_tile_for_action} 的操作: {response_type}")

        if all(response is not None for response in self.action_responses.values()):
            self._resolve_pending_actions_logic()
        return

    def _resolve_pending_actions_logic(self):  # 保持不变
        if not self.action_pending: return
        discarded_tile = self._pending_action_info["discarded_tile"]
        discarder_id = self._pending_action_info["discarder_id"]
        discarder_idx = self.get_player_index_by_id(discarder_id)

        hu_pid, gang_po, pong_po = None, None, None
        for i in range(1, self.num_players):
            p_idx = (discarder_idx + i) % self.num_players
            p_obj = self.players[p_idx]
            resp = self.action_responses.get(p_obj.player_id)
            if resp == "hu":
                if hu_pid is None: hu_pid = p_obj.player_id
        if hu_pid:
            winner = self.get_player_by_id(hu_pid)
            self.end_game(f"{winner.name} 接炮胡！", hu_pid, discarded_tile)
            self._reset_action_state_logic();
            return

        for i in range(1, self.num_players):  # 再看杠/碰
            p_idx = (discarder_idx + i) % self.num_players
            p_obj = self.players[p_idx]
            resp = self.action_responses.get(p_obj.player_id)
            if resp == "gang":
                if gang_po is None: gang_po = p_obj
            if resp == "pong" and gang_po is None:
                if pong_po is None: pong_po = p_obj

        action_taken = False
        if gang_po:
            if gang_po.perform_gang("ming", discarded_tile, discarded_tile, self.game_rules):
                self.broadcast_message({"type": "player_ganged", "player_id": gang_po.player_id, "tile": discarded_tile,
                                        "gang_type": "ming", "melds": gang_po.melds})
                self.current_turn = self.get_player_index_by_id(gang_po.player_id)
                self._draw_and_handle_gang_replacement_logic(gang_po)
                action_taken = True
        elif pong_po:
            if pong_po.perform_pong(discarded_tile):
                self.broadcast_message({"type": "player_ponged", "player_id": pong_po.player_id, "tile": discarded_tile,
                                        "melds": pong_po.melds})
                self.current_turn = self.get_player_index_by_id(pong_po.player_id)
                self._next_prompt_info = (pong_po.player_id,
                                          {"type": "action_prompt", "actions": ["discard"], "from_pong_gang": True})
                action_taken = True

        self._reset_action_state_logic()
        if not action_taken: self._advance_turn_logic()

    def _reset_action_state_logic(self):  # 保持不变
        self.action_pending = False;
        self.action_responses = {};
        self._pending_action_info = None
        for p in self.players: p.can_pong = False; p.can_gang = False; p.can_hu_discard = False

    def _advance_turn_logic(self):  # 保持不变
        if self.game_state != "playing": return
        self.current_turn = (self.current_turn + 1) % self.num_players
        if self.deck.remaining() == 0: self.end_game("牌摸完了 (流局)"); return
        self._start_player_turn_logic(self.current_turn)

    def end_game(self, reason, winner_id=None, winning_tile=None):  # 移除joker_tile
        if self.game_state == "finished": return
        self.game_state = "finished"
        self.winning_player_id = winner_id
        self.winning_tile = winning_tile
        logger.info(f"--- 游戏结束！ 原因: {reason} ---")
        # ... (日志部分不变) ...
        final_hands_info = {}
        for p in self.players:
            final_hands_info[str(p.player_id)] = {
                "hand": p.hand, "melds": p.melds,
                "is_listening": p.is_listening,
                "listening_tiles": p.listening_tiles if p.is_listening else []
            }
        final_state_msg = {
            "type": "game_over", "reason": reason,
            "winning_player_id": winner_id, "winning_tile": winning_tile,
            "final_hands": final_hands_info,
            # "joker_tile": self.game_rules.joker_tile # 已移除
        }
        self.broadcast_message(final_state_msg)

    def send_message_to_player(self, player_id, message):
        pass

    def broadcast_message(self, message):
        pass