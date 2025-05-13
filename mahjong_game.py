# mahjong_game.py
# 定义麻将游戏的核心逻辑，包括牌堆、玩家、游戏流程等

import random
from collections import Counter
import copy  # 确保 deepcopy 可用
import logging
from mahjong_common import (
    ALL_TILES_SUIT, ALL_TILES_WIND, ALL_TILES_DRAGON,
    TILES_PER_TYPE, INITIAL_HAND_SIZE, sort_tiles, tile_sort_key,
    is_triplet, is_quad, is_pair,
    # send_json, receive_json # 仅用于类型提示，本文件不直接使用
)

# 获取此模块的日志记录器
logger = logging.getLogger(__name__)


class Player:
    """表示一个玩家及其状态和操作。"""

    def __init__(self, player_id, name):
        """初始化玩家。"""
        self.player_id = player_id
        self.name = name
        self.hand = []  # 手牌 (字符串列表)
        self.melds = []  # 亮出的牌组 (碰、杠) (列表的列表)
        self.discarded = []  # 打出的牌 (字符串列表)
        self.is_listening = False  # 是否已叫听
        self.listening_tiles = []  # 叫听的牌 (字符串列表)

        # 内部状态标记，由 Game 类在检查时设置
        self.can_hu_zimo = False  # 是否可自摸
        self.can_pong = False  # 是否可碰最后弃牌
        self.can_gang = False  # 是否可明杠最后弃牌
        self.can_hu_discard = False  # 是否可胡最后弃牌
        self.possible_an_gangs = []  # 暗杠的牌 (列表)
        self.possible_bu_gangs = []  # 补杠的信息 (元组列表: (meld_index, tile))

        # 过水不胡相关状态
        self.passed_hu_on_discard = None  # 记录上次过水不胡的弃牌
        self.passed_hu_on_zimo_opportunity = False  # 是否在自摸机会时选择了过

    def add_tile(self, tile):
        """向手牌中添加一张牌并排序。"""
        self.hand.append(tile)
        self.hand = sort_tiles(self.hand)

    def remove_tile(self, tile):
        """从手牌中移除一张指定的牌。"""
        try:
            self.hand.remove(tile)
            self.hand = sort_tiles(self.hand)
            return True
        except ValueError:
            logger.warning(f"玩家 {self.name} ({self.player_id}) 尝试移除不存在的牌: {tile} 从手牌 {self.hand}")
            return False

    def can_pong_tile(self, tile_to_check, game_rules=None):
        """检查手牌是否有两张或以上与指定牌相同（碰牌的基本条件）。"""
        # 混儿牌不能用于碰 (通常规则)
        if game_rules and game_rules.joker_tile and tile_to_check == game_rules.joker_tile:
            return False
        return self.hand.count(tile_to_check) >= 2

    def perform_pong(self, tile_to_pong):
        """执行碰牌操作：从手牌移除两张，添加到亮牌区。"""
        if self.hand.count(tile_to_pong) < 2:
            logger.warning(f"玩家 {self.name} 尝试碰牌 {tile_to_pong} 但手牌不足。")
            return False
        self.remove_tile(tile_to_pong)
        self.remove_tile(tile_to_pong)
        self.melds.append(sort_tiles([tile_to_pong, tile_to_pong, tile_to_pong]))
        self.melds = sorted(self.melds, key=lambda m: tile_sort_key(m[0]))
        return True

    def find_possible_gangs(self, tile_from_discard=None, game_rules=None):
        """查找当前手牌和亮牌组合下所有可能的杠牌选项（暗杠、补杠、明杠）。"""
        possible_an_gangs = []
        possible_bu_gangs = []
        possible_ming_gangs = []

        hand_counts = Counter(self.hand)
        joker = game_rules.joker_tile if game_rules else None
        num_jokers_in_hand = hand_counts.get(joker, 0) if joker else 0

        # 查找暗杠 (手牌中有4张相同的非混儿牌，或者3张+1混儿，2张+2混儿等)
        for tile_val, count in hand_counts.items():
            if joker and tile_val == joker: continue  # 混儿本身不能发起暗杠的主体
            if count == 4:
                possible_an_gangs.append(tile_val)
            elif joker and count == 3 and num_jokers_in_hand >= 1:  # 3张一样的 + 1混儿
                possible_an_gangs.append(tile_val)
            elif joker and count == 2 and num_jokers_in_hand >= 2:  # 2张一样的 + 2混儿
                possible_an_gangs.append(tile_val)
            elif joker and count == 1 and num_jokers_in_hand >= 3:  # 1张一样的 + 3混儿
                possible_an_gangs.append(tile_val)

        # 如果允许混儿暗杠 (4个混儿)
        if joker and num_jokers_in_hand == 4 and game_rules.allow_joker_an_gang:
            possible_an_gangs.append(joker)  # 可以杠四个混儿

        # 查找补杠 (手牌中有1张与已碰牌组相同的牌，或用混儿补)
        for i, meld in enumerate(self.melds):
            if is_triplet(meld):  # 必须是碰出的刻子
                tile_in_meld = meld[0]
                if joker and tile_in_meld == joker: continue  # 通常不允许补杠混儿刻子

                if hand_counts.get(tile_in_meld, 0) >= 1:  # 手里有这张牌
                    possible_bu_gangs.append((i, tile_in_meld))
                elif joker and num_jokers_in_hand >= 1:  # 手里没这张牌，但有混儿
                    possible_bu_gangs.append((i, tile_in_meld))  # 用混儿补杠

        # 查找明杠 (手牌中有3张与弃牌相同的牌，或用混儿凑)
        if tile_from_discard:
            if joker and tile_from_discard == joker:  # 通常不允许杠别人打出的混儿
                pass
            else:
                non_joker_count = hand_counts.get(tile_from_discard, 0)
                if non_joker_count == 3:  # 手里三张一样的
                    possible_ming_gangs.append(tile_from_discard)
                elif joker and non_joker_count == 2 and num_jokers_in_hand >= 1:  # 手里两张 + 1混儿
                    possible_ming_gangs.append(tile_from_discard)
                elif joker and non_joker_count == 1 and num_jokers_in_hand >= 2:  # 手里一张 + 2混儿
                    possible_ming_gangs.append(tile_from_discard)
                elif joker and non_joker_count == 0 and num_jokers_in_hand >= 3:  # 手里没有 + 3混儿
                    possible_ming_gangs.append(tile_from_discard)

        self.possible_an_gangs = list(set(possible_an_gangs))  # 去重
        self.possible_bu_gangs = list(set(possible_bu_gangs))  # 去重
        self.possible_ming_gangs = list(set(possible_ming_gangs))  # 去重
        return self.possible_an_gangs, self.possible_bu_gangs, self.possible_ming_gangs

    def perform_gang(self, gang_type, tile_info, tile_discarded_for_ming_gang=None, game_rules=None):
        """
        执行杠牌操作。
        tile_info: 对于'an'是杠的牌, 对于'bu'是(meld_index, tile_to_complete_meld), 对于'ming'是杠的牌.
        tile_discarded_for_ming_gang: 明杠时，这张是别人打出的牌。
        """
        joker = game_rules.joker_tile if game_rules else None
        original_hand = self.hand[:]  # 备份手牌，以便失败时恢复

        try:
            if gang_type == "an":
                target_tile = tile_info
                # 移除4张 (可能是目标牌本身或混儿)
                removed_count = 0
                # 优先移除目标牌
                for _ in range(self.hand.count(target_tile)):
                    if removed_count < 4:
                        self.remove_tile(target_tile)
                        removed_count += 1
                    else:
                        break
                # 再用混儿补齐
                if joker and removed_count < 4:
                    for _ in range(self.hand.count(joker)):
                        if removed_count < 4:
                            self.remove_tile(joker)
                            removed_count += 1
                        else:
                            break

                if removed_count != 4:
                    logger.error(f"暗杠时手牌移除不足4张: {target_tile} (玩家 {self.name})")
                    self.hand = original_hand  # 恢复手牌
                    return False
                self.melds.append(sort_tiles([target_tile] * 4))  # 暗杠显示为目标牌
                logger.debug(f"{self.name} 执行暗杠: {target_tile}")

            elif gang_type == "bu":
                meld_index, tile_to_complete_meld = tile_info
                # 检查目标亮牌组是否有效
                if not (0 <= meld_index < len(self.melds) and is_triplet(self.melds[meld_index]) and
                        self.melds[meld_index][0] == tile_to_complete_meld):
                    logger.error(f"补杠目标亮牌组无效 {tile_info} (玩家 {self.name})")
                    return False

                # 移除一张 (目标牌或混儿)
                if self.hand.count(tile_to_complete_meld) >= 1:
                    self.remove_tile(tile_to_complete_meld)
                elif joker and self.hand.count(joker) >= 1:
                    self.remove_tile(joker)
                else:
                    logger.error(f"补杠时手牌中没有牌 {tile_to_complete_meld} 或混儿 (玩家 {self.name})")
                    self.hand = original_hand
                    return False
                self.melds[meld_index].append(tile_to_complete_meld)  # 杠后牌组还是显示目标牌
                self.melds[meld_index] = sort_tiles(self.melds[meld_index])
                logger.debug(f"{self.name} 执行补杠: {tile_to_complete_meld}")

            elif gang_type == "ming":
                target_tile = tile_info  # 这是要杠的牌的种类 (别人打出的牌，或自己手牌凑的)
                # 从手牌移除3张 (目标牌或混儿)
                removed_count = 0
                # 优先移除目标牌
                for _ in range(self.hand.count(target_tile)):
                    if removed_count < 3:
                        self.remove_tile(target_tile);
                        removed_count += 1
                    else:
                        break
                # 再用混儿补齐
                if joker and removed_count < 3:
                    for _ in range(self.hand.count(joker)):
                        if removed_count < 3:
                            self.remove_tile(joker);
                            removed_count += 1
                        else:
                            break
                if removed_count != 3:
                    logger.error(f"明杠时手牌移除不足3张: {target_tile} (玩家 {self.name})")
                    self.hand = original_hand
                    return False
                # tile_discarded_for_ming_gang 是别人打的那张牌，已经不在手牌里
                self.melds.append(sort_tiles([target_tile] * 4))  # 明杠显示为目标牌
                logger.debug(f"{self.name} 执行明杠: {target_tile} (杠的是 {tile_discarded_for_ming_gang})")
            else:
                logger.error(f"无效杠类型 {gang_type} (玩家 {self.name})")
                return False

            self.melds = sorted(self.melds, key=lambda m: tile_sort_key(m[0]))
            return True
        except Exception as e:
            logger.exception(f"执行杠操作时发生错误 (玩家 {self.name}, 类型 {gang_type}, 信息 {tile_info})")
            self.hand = original_hand  # 发生异常时尝试恢复
            return False

    def get_tile_type_and_value(self, tile_str):
        if tile_str is None: return None, None
        parts = tile_str.split('_')
        suit = parts[0]
        value_str = parts[1]
        if suit in ["wan", "tiao", "tong"]:
            try:
                return suit, int(value_str)
            except ValueError:
                return suit, value_str  # "undefined" or other non-int
        return suit, value_str

    def _can_form_melds_recursive(self, current_tiles_list, num_jokers, game_rules):
        """
        递归检查剩余牌 (不含将牌) 是否能组成面子。
        current_tiles_list: 当前剩余的非混儿牌列表。
        num_jokers: 当前可用的混儿牌数量。
        """
        if not current_tiles_list:  # 所有非混儿牌都已组成面子
            return True  # 成功

        # 对当前牌进行排序和计数，确保处理顺序
        sorted_current_tiles = sort_tiles(current_tiles_list)
        counts = Counter(sorted_current_tiles)
        first_tile = sorted_current_tiles[0]

        # 1. 尝试移除刻子 (AAA, AAJ, AJJ, JJJ)
        # 1a. 三张实牌 (AAA)
        if counts.get(first_tile, 0) >= 3:
            remaining_after_triplet = sorted_current_tiles[3:]
            if self._can_form_melds_recursive(remaining_after_triplet, num_jokers, game_rules):
                return True
        # 1b. 两张实牌 + 1混儿 (AAJ)
        if counts.get(first_tile, 0) >= 2 and num_jokers >= 1:
            remaining_after_triplet_j1 = [t for i, t in enumerate(sorted_current_tiles) if t != first_tile or i >= 2]
            if self._can_form_melds_recursive(remaining_after_triplet_j1, num_jokers - 1, game_rules):
                return True
        # 1c. 一张实牌 + 2混儿 (AJJ)
        if counts.get(first_tile, 0) >= 1 and num_jokers >= 2:
            remaining_after_triplet_j2 = [t for i, t in enumerate(sorted_current_tiles) if t != first_tile or i >= 1]
            if self._can_form_melds_recursive(remaining_after_triplet_j2, num_jokers - 2, game_rules):
                return True
        # 1d. 三张混儿 (JJJ) - 这种情况由外部的混儿刻子处理，或在没有实牌时

        # 2. 尝试移除顺子 (ABC, AJC, ABJ, JBC) - 仅对万、条、筒
        tile_type, tile_value = self.get_tile_type_and_value(first_tile)
        if tile_type in ["wan", "tiao", "tong"] and isinstance(tile_value, int) and tile_value <= 7:  # 防止 89X, 9XX
            t1_str, t2_str, t3_str = first_tile, f"{tile_type}_{tile_value + 1}", f"{tile_type}_{tile_value + 2}"

            # 2a. 实牌顺子 (ABC)
            if counts.get(t1_str, 0) >= 1 and counts.get(t2_str, 0) >= 1 and counts.get(t3_str, 0) >= 1:
                temp_list = list(sorted_current_tiles)
                temp_list.remove(t1_str);
                temp_list.remove(t2_str);
                temp_list.remove(t3_str)
                if self._can_form_melds_recursive(temp_list, num_jokers, game_rules):
                    return True
            # 2b. 两实一混 (A_C + J, AB_ + J, _BC + J)
            if num_jokers >= 1:
                # A_C + J (缺B)
                if counts.get(t1_str, 0) >= 1 and counts.get(t3_str, 0) >= 1:
                    temp_list = list(sorted_current_tiles)
                    temp_list.remove(t1_str);
                    temp_list.remove(t3_str)
                    if self._can_form_melds_recursive(temp_list, num_jokers - 1, game_rules):
                        return True
                # AB_ + J (缺C)
                if counts.get(t1_str, 0) >= 1 and counts.get(t2_str, 0) >= 1:
                    temp_list = list(sorted_current_tiles)
                    temp_list.remove(t1_str);
                    temp_list.remove(t2_str)
                    if self._can_form_melds_recursive(temp_list, num_jokers - 1, game_rules):
                        return True
                # _BC + J (缺A) - 这个情况会被 t2_str 的迭代覆盖，但为了清晰可以加上
                # 这种情况在迭代到 t2_str 时，会尝试 t2, t3, J

            # 2c. 一实两混 (A__ + JJ, _B_ + JJ, __C + JJ)
            if num_jokers >= 2:
                # A__ + JJ (有A，缺BC)
                if counts.get(t1_str, 0) >= 1:
                    temp_list = list(sorted_current_tiles);
                    temp_list.remove(t1_str)
                    if self._can_form_melds_recursive(temp_list, num_jokers - 2, game_rules):
                        return True
                # _B_ + JJ (有B，缺AC) - 在迭代到t2_str时处理
                # __C + JJ (有C，缺AB) - 在迭代到t3_str时处理

        # 3. 如果前面都无法成功移除面子，尝试用3个混儿组成一个面子 (如果允许)
        if game_rules.allow_joker_meld and num_jokers >= 3:
            if self._can_form_melds_recursive(list(sorted_current_tiles), num_jokers - 3, game_rules):  # 实牌不变，消耗3个混儿
                return True

        return False  # 无法从当前牌组成面子

    def check_standard_win(self, tiles_for_check, game_rules):
        """检查是否是标准胡牌 (m * 面子 + 1 * 将)。"""
        if len(tiles_for_check) % 3 != 2 or len(tiles_for_check) < 2:
            return False

        joker = game_rules.joker_tile if game_rules else None
        non_joker_tiles = [t for t in tiles_for_check if t != joker]
        num_jokers = tiles_for_check.count(joker) if joker else 0

        # 尝试每一种可能的将牌 (包括用混儿做将)
        unique_non_joker_tiles = sort_tiles(list(set(non_joker_tiles)))

        # 1. 尝试实牌作将 (AA)
        for pair_tile in unique_non_joker_tiles:
            if non_joker_tiles.count(pair_tile) >= 2:
                remaining_tiles = list(non_joker_tiles)
                remaining_tiles.remove(pair_tile)
                remaining_tiles.remove(pair_tile)
                if self._can_form_melds_recursive(remaining_tiles, num_jokers, game_rules):
                    return True

        # 2. 尝试一实牌一混儿作将 (AJ)
        if joker and num_jokers >= 1:
            for pair_tile in unique_non_joker_tiles:
                if non_joker_tiles.count(pair_tile) >= 1:
                    remaining_tiles = list(non_joker_tiles)
                    remaining_tiles.remove(pair_tile)
                    if self._can_form_melds_recursive(remaining_tiles, num_jokers - 1, game_rules):
                        return True

        # 3. 尝试两混儿作将 (JJ) - 如果规则允许
        if joker and num_jokers >= 2 and game_rules.allow_joker_pair:
            if self._can_form_melds_recursive(list(non_joker_tiles), num_jokers - 2, game_rules):
                return True
        return False

    def can_hu_tile(self, tile_to_win=None, is_zimo=False, game_rules=None):
        """检查是否可以胡牌。"""
        all_tiles_for_check = []
        all_tiles_for_check.extend(self.hand)
        for meld_group in self.melds:  # 亮出的牌不参与混儿的灵活组合，它们已经是固定的面子
            all_tiles_for_check.extend(meld_group)

        if is_zimo:  # tile_to_win 应该已经在 self.hand 里
            pass
        elif tile_to_win:
            all_tiles_for_check.append(tile_to_win)

        # 基本数量检查
        if len(all_tiles_for_check) < 2: return False  # 至少要有一对

        # 检查过水不胡 (如果启用了该规则)
        if game_rules and game_rules.enable_passed_hu_rule:
            if is_zimo:  # 自摸胡
                if self.passed_hu_on_zimo_opportunity and tile_to_win:  # 如果之前自摸过水，现在又摸到一样的，看规则
                    # 复杂的过水规则：如果摸牌后手牌组合变化导致能胡新的牌，则可以胡。
                    # 简化处理：如果之前自摸过水，这次自摸的牌和上次能胡的牌一样，则不能胡。
                    # （这个简化可能不完全符合所有过水规则）
                    # 假设如果 passed_hu_on_zimo_opportunity 为 true，则暂时不能胡。
                    # 更精细的判断需要记录当时具体能胡哪些牌。
                    # 此处简化：如果上次自摸过水，则这次不能立即胡。Game逻辑中应在打牌后清除此标记。
                    # logger.debug(f"玩家 {self.name} 曾自摸过水，当前尝试自摸 {tile_to_win}，暂时禁止。")
                    # return False # 这个逻辑需要Game类更细致地管理状态
                    pass  # Game 类会在外部处理 passed_hu_on_zimo_opportunity 的重置
            else:  # 点炮胡
                if self.passed_hu_on_discard and self.passed_hu_on_discard == tile_to_win:
                    # 如果之前对这张弃牌选择了过水，则不能胡这张牌
                    logger.debug(f"玩家 {self.name} 对牌 {tile_to_win} 已过水不胡。")
                    return False

        # 1. 检查标准胡牌 (m * 面子 + 1 * 将)
        if self.check_standard_win(all_tiles_for_check, game_rules):
            logger.debug(f"标准胡牌结构检查通过 (check_standard_win): {all_tiles_for_check}")
            return True

        # 2. 检查七对 (需要14张牌，且没有碰杠，混儿可以当任意牌凑对)
        joker = game_rules.joker_tile if game_rules else None
        if len(all_tiles_for_check) == 14 and not self.melds:
            non_joker_tiles_qidu = [t for t in all_tiles_for_check if t != joker]
            num_jokers_qidu = all_tiles_for_check.count(joker) if joker else 0
            counts_qidu = Counter(non_joker_tiles_qidu)

            pairs_needed = 7
            pairs_found = 0
            jokers_used_for_pairs = 0

            for tile_val, count in counts_qidu.items():
                if count == 2:
                    pairs_found += 1
                elif count == 4:
                    pairs_found += 2  # 四张算两对
                elif count == 1 and num_jokers_qidu - jokers_used_for_pairs >= 1:
                    pairs_found += 1;
                    jokers_used_for_pairs += 1
                elif count == 3 and num_jokers_qidu - jokers_used_for_pairs >= 1:  # 三张+1混儿=两对
                    pairs_found += 2;
                    jokers_used_for_pairs += 1
                # 其他单张或三张无混儿补的情况，不能构成七对中的对子

            # 剩余的混儿两两组成对子
            if num_jokers_qidu - jokers_used_for_pairs >= 0:
                pairs_found += (num_jokers_qidu - jokers_used_for_pairs) // 2

            if pairs_found == pairs_needed:
                logger.debug(f"七对检查通过: {all_tiles_for_check} (混儿: {num_jokers_qidu})")
                return True

        # TODO: 检查河南麻将其他特殊牌型，如十三幺（如果适用）等
        # 例如十三幺:
        # yaojiu_tiles = ["wan_1", "wan_9", "tiao_1", "tiao_9", "tong_1", "tong_9",
        #                 "feng_dong", "feng_nan", "feng_xi", "feng_bei",
        #                 "jian_zhong", "jian_fa", "jian_bai"]
        # if len(all_tiles_for_check) == 14 and not self.melds:
        #     is_shisanyao = True
        #     counts_all = Counter(all_tiles_for_check)
        #     pair_found_for_shisan = False
        #     num_jokers_shisan = counts_all.get(joker, 0) if joker else 0
        #     jokers_used_shisan = 0
        #     unique_yaojiu_present = 0

        #     for yj_tile in yaojiu_tiles:
        #         if counts_all.get(yj_tile, 0) >= 1:
        #             unique_yaojiu_present +=1
        #         elif num_jokers_shisan - jokers_used_shisan >= 1: #用混儿代替缺的幺九牌
        #             unique_yaojiu_present +=1
        #             jokers_used_shisan +=1
        #         else:
        #             is_shisanyao = False; break

        #     if is_shisanyao and unique_yaojiu_present == 13:
        #         # 检查将牌：必须是13种幺九牌中的一个（可以是实牌对，或单张+混儿，或两个混儿代替一个幺九牌做将）
        #         # 或者多出来的那张牌 + 剩余的混儿能凑成一个幺九牌的对子
        #         # 这个逻辑比较复杂，需要仔细实现
        #         # 简化：如果凑齐了13种单张后，剩下的牌（包括未用完的混儿）能形成任意一个幺九牌的对子就算。
        #         # 已经消耗了jokers_used_shisan个混儿去凑13张单牌。
        #         # 剩下的牌是 all_tiles_for_check 减去已经确认的13种单张幺九牌（实牌或混儿代替的）
        #         # 这部分逻辑待细化
        #         pass

        return False

    def find_listening_tiles(self, game_rules, possible_draw_tiles_list=None):
        """找出当前手牌听哪些牌。"""
        if len(self.hand) % 3 != 1:
            logger.debug(f"玩家 {self.name} 手牌 {len(self.hand)} 张不满足听牌计算条件 (3n+1)。")
            return []

        if possible_draw_tiles_list is None:
            all_game_tiles_unique = list(set(ALL_TILES_SUIT + ALL_TILES_WIND + ALL_TILES_DRAGON))
            if game_rules.joker_tile:  # 听牌时通常不考虑摸到混儿就能胡的情况，除非规则特殊
                pass  # all_game_tiles_unique.append(game_rules.joker_tile)
        else:
            all_game_tiles_unique = list(set(possible_draw_tiles_list))

        listening = set()
        # 临时清除过水状态，避免影响听牌判断的内部can_hu_tile调用
        original_passed_discard = self.passed_hu_on_discard
        original_passed_zimo = self.passed_hu_on_zimo_opportunity
        self.passed_hu_on_discard = None
        self.passed_hu_on_zimo_opportunity = False

        for test_tile in all_game_tiles_unique:
            if game_rules.joker_tile and test_tile == game_rules.joker_tile:
                # 规则：听牌时，是否计算摸到混儿能胡的情况？
                # 通常，听的是“非混儿”牌。如果摸到混儿，混儿可以变成任何所听的牌。
                # 所以这里一般不把混儿作为直接的听牌对象。
                continue

            # 检查 (当前手牌 + 这张牌) 是否能胡
            if self.can_hu_tile(tile_to_win=test_tile, is_zimo=False, game_rules=game_rules):
                listening.add(test_tile)

        # 恢复过水状态
        self.passed_hu_on_discard = original_passed_discard
        self.passed_hu_on_zimo_opportunity = original_passed_zimo

        self.listening_tiles = sort_tiles(list(listening))
        logger.debug(
            f"玩家 {self.name} 计算听牌结果 (手牌 {self.hand}, 亮牌 {self.melds}): {self.listening_tiles} using rules: joker='{game_rules.joker_tile}', passed_hu='{game_rules.enable_passed_hu_rule}'")
        return self.listening_tiles


class GameRules:
    """存储游戏特定规则的配置类。"""

    def __init__(self,
                 enable_passed_hu_rule=True,
                 joker_tile=None,  # 例如 "jian_zhong" (红中做混儿)
                 allow_joker_pair=True,  # 混儿是否能做将
                 allow_joker_meld=True,  # 混儿是否能单独组成面子 (例如3个混儿算一刻/顺)
                 allow_joker_an_gang=False,  # 是否允许四个混儿暗杠
                 include_winds_dragons=True  # 是否包含风牌和箭牌
                 ):
        self.enable_passed_hu_rule = enable_passed_hu_rule
        self.joker_tile = joker_tile
        self.allow_joker_pair = allow_joker_pair
        self.allow_joker_meld = allow_joker_meld  # 指3个混儿组成一个面子
        self.allow_joker_an_gang = allow_joker_an_gang
        self.include_winds_dragons = include_winds_dragons
        logger.info(f"游戏规则初始化: 过水不胡={enable_passed_hu_rule}, 混儿牌='{joker_tile}', "
                    f"混儿做将={allow_joker_pair}, 3混儿成面子={allow_joker_meld}, "
                    f"4混儿暗杠={allow_joker_an_gang}, 含风箭={include_winds_dragons}")


class Deck:
    """表示牌堆及其操作。"""

    def __init__(self, game_rules):  # Deck 也需要知道游戏规则，主要为了混儿牌
        self.tiles = []
        self.game_rules = game_rules
        # 添加万、条、筒
        for tile_str in ALL_TILES_SUIT: self.tiles.extend([tile_str] * TILES_PER_TYPE)
        # 根据配置添加风牌和箭牌
        if self.game_rules.include_winds_dragons:
            for tile_str in ALL_TILES_WIND: self.tiles.extend([tile_str] * TILES_PER_TYPE)
            for tile_str in ALL_TILES_DRAGON: self.tiles.extend([tile_str] * TILES_PER_TYPE)

        # 如果有混儿牌，需要确保混儿牌的数量和来源正确
        # 假设混儿牌是从已有的牌中指定一张，而不是额外加入的。
        # 例如，如果红中是混儿，那么牌堆中正常的红中牌就是混儿。
        # 如果混儿牌是额外加入的（比如大小王），则需要在这里添加。
        # 当前代码假设混儿牌是标准牌中的一张。

        random.shuffle(self.tiles)
        self.initial_size = len(self.tiles)
        logger.debug(f"牌堆初始化完成，总共 {self.initial_size} 张牌。混儿牌: {self.game_rules.joker_tile}")

    def draw_tile(self):
        if self.tiles: return self.tiles.pop(0)
        logger.warning("尝试从空牌堆摸牌 (顶部)。")
        return None

    def draw_from_end(self):
        if self.tiles: return self.tiles.pop()
        logger.warning("尝试从空牌堆摸牌 (末尾)。")
        return None

    def remaining(self):
        return len(self.tiles)


class Game:
    """游戏主类，管理游戏流程、状态和玩家交互。"""

    def __init__(self, num_players=4, game_rules_config=None):  # 接受规则配置
        if not 2 <= num_players <= 4:
            raise ValueError("玩家人数必须在2到4之间")
        self.num_players = num_players
        # 初始化游戏规则
        self.game_rules = GameRules(**game_rules_config) if game_rules_config else GameRules()

        self.deck = None
        self.players = []
        self.current_turn = 0
        self.discard_pile = []
        self.last_discarded_tile = None
        self.last_discarder_id = None
        self.game_state = "waiting"  # waiting, dealing, playing, finished
        self.winning_player_id = None
        self.winning_tile = None  # 胡的牌或"自摸"或"混儿自摸"
        self.action_pending = False
        self.action_responses = {}
        self._pending_action_info = None
        self._next_prompt_info = None

        self.all_game_tiles_list = list(set(ALL_TILES_SUIT +
                                            (ALL_TILES_WIND if self.game_rules.include_winds_dragons else []) +
                                            (ALL_TILES_DRAGON if self.game_rules.include_winds_dragons else [])))
        logger.info(f"游戏实例初始化: {num_players}人。规则见 GameRules 日志。")

    def add_player(self, player_obj):  # 参数改为 player_obj
        if self.game_state != "waiting":
            logger.warning(f"尝试在游戏状态 '{self.game_state}' 时添加玩家 {player_obj.name}")
            return False
        if len(self.players) < self.num_players:
            self.players.append(player_obj)
            return True
        else:
            logger.warning(f"尝试添加玩家 {player_obj.name} 但游戏实例已满.")
            return False

    def start_game(self):
        if len(self.players) != self.num_players:
            logger.error(f"玩家数量不足 ({len(self.players)}/{self.num_players})，无法开始游戏。")
            return False

        logger.info("游戏开始发牌...")
        self.game_state = "dealing"
        self.deck = Deck(self.game_rules)  # 使用规则初始化牌堆

        # 发初始手牌
        for _ in range(INITIAL_HAND_SIZE):
            for player in self.players:
                tile = self.deck.draw_tile()
                if tile:
                    player.add_tile(tile)
                else:
                    logger.error("发牌时牌堆耗尽！")
                    self.end_game("发牌时牌不够")
                    return False

        # 如果有混儿牌，可以考虑在发牌后翻一张牌作为指示牌，其下一张为混儿 (宝牌规则)
        # 或者直接在 GameRules 中指定。当前是后者。

        self.current_turn = 0  # 简化：第一个玩家为庄家
        dealer = self.players[self.current_turn]
        logger.info(f"发牌完成。庄家是 {dealer.name} ({dealer.player_id})。")
        # 重置所有玩家的过水状态
        for p in self.players:
            p.passed_hu_on_discard = None
            p.passed_hu_on_zimo_opportunity = False

        self.game_state = "playing"
        if not self.start_player_turn(self.current_turn):
            logger.error("开始庄家回合失败。")
            return False
        return True

    def get_player_by_id(self, player_id):
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None

    def get_player_index_by_id(self, player_id):
        for i, player in enumerate(self.players):
            if player.player_id == player_id:
                return i
        return -1

    def get_state_for_player(self, player_id_to_get_state_for):  # 避免和变量名冲突
        player_obj = self.get_player_by_id(player_id_to_get_state_for)
        if not player_obj:
            logger.error(f"尝试为不存在的玩家ID {player_id_to_get_state_for} 获取状态。")
            return None
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
                    "listening_tiles": p.listening_tiles if p.player_id == player_id_to_get_state_for else [],
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
            "joker_tile": self.game_rules.joker_tile,  # 让客户端知道混儿牌
        }
        return state

    def start_player_turn(self, player_index):
        if self.game_state != "playing": return False
        if not (0 <= player_index < len(self.players)): return False

        player = self.players[player_index]
        # 在新回合开始时，如果启用了过水不胡，且玩家之前自摸过水，重置该标记
        # 因为他即将摸新牌，改变了局面。对弃牌的过水状态由打出新牌清除。
        if self.game_rules.enable_passed_hu_rule:
            player.passed_hu_on_zimo_opportunity = False  # 新摸牌，重置自摸过水

        logger.info(f"--- 轮到 {player.name} ({player.player_id}) 回合 ---")
        drawn_tile = self.deck.draw_tile()
        if not drawn_tile:
            self.end_game("牌摸完了 (流局)")
            return False

        player.add_tile(drawn_tile)
        logger.debug(f"{player.name} 摸到一张牌: {drawn_tile} (手牌: {player.hand})")

        player.can_hu_zimo = False
        possible_an, possible_bu, _ = player.find_possible_gangs(game_rules=self.game_rules)
        actions = []

        if player.can_hu_tile(tile_to_win=drawn_tile, is_zimo=True, game_rules=self.game_rules):
            actions.append("hu")
            player.can_hu_zimo = True

        if not player.is_listening:  # 听后不能杠 (简化规则)
            if possible_an or possible_bu: actions.append("gang")

        # 叫听的提示时机通常是打牌前，所以不在这里立即提示听
        # 除非是特殊的“摸到宝马上听”的规则

        if "hu" not in actions or len(player.hand) > 0:  # 即使能胡，也通常允许打牌
            actions.append("discard")

        # 如果玩家处于“过水不胡（点炮）”状态，摸牌后这个状态解除
        # 因为手牌组合可能变化，或者可以换听。
        # player.passed_hu_on_discard = None # 在打牌后清除更合适

        message = {
            "type": "action_prompt", "actions": actions, "drawn_tile": drawn_tile,
            "possible_an_gangs": possible_an, "possible_bu_gangs": possible_bu,
            "is_gang_replacement": False
        }
        self._next_prompt_info = (player.player_id, message)
        logger.debug(f"为玩家 {player.player_id} 设置行动提示: {actions}")
        return True

    def handle_player_action(self, player_id, action_data):
        player_index = self.get_player_index_by_id(player_id)
        if player_index == -1 or player_index != self.current_turn:
            logger.warning(f"收到来自非当前玩家 {player_id} 的行动请求。")
            return {"success": False, "message": "不是你的回合"}

        player = self.players[player_index]
        action_type = action_data.get("action_type")
        logger.info(f"玩家 {player.name} ({player_id}) 请求执行操作: {action_type}")

        if action_type == "discard":
            tile_to_discard = action_data.get("tile")
            drawn_tile_client = action_data.get("drawn_tile")  # 客户端认为摸到的牌

            if not tile_to_discard or tile_to_discard not in player.hand:
                logger.error(f"玩家 {player.name} 尝试打出无效/不存在的牌: {tile_to_discard} (手牌: {player.hand})")
                return {"success": False, "message": "无效弃牌"}

            if player.is_listening and drawn_tile_client and tile_to_discard != drawn_tile_client:
                # 服务器应该再次校验，摸到的牌是服务器记录的，而不是客户端传的
                # 此处简化：信任客户端在听牌时打出的是摸到的牌
                logger.info(f"玩家 {player.name} 已叫听，打出摸到的牌: {tile_to_discard}")

            player.remove_tile(tile_to_discard)
            # 打牌后，清除该玩家所有类型的过水状态
            if self.game_rules.enable_passed_hu_rule:
                player.passed_hu_on_discard = None
                player.passed_hu_on_zimo_opportunity = False
                logger.debug(f"玩家 {player.name} 打牌后清除过水状态。")

            self.discard_pile.append(tile_to_discard)
            player.discarded.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discarder_id = player_id
            logger.info(f"{player.name} 打出了 {tile_to_discard}")

            self.broadcast_message({"type": "player_discarded", "player_id": player_id, "tile": tile_to_discard})
            self.check_other_players_actions()  # 检查响应
            return {"success": True}

        elif action_type == "hu":  # 自摸胡
            # 服务器再次验证
            if player.can_hu_tile(is_zimo=True, game_rules=self.game_rules):  # 自摸时 tile_to_win 通常是None或已在手牌中
                win_desc = "自摸"
                if self.game_rules.joker_tile and player.hand.count(self.game_rules.joker_tile) > 0:
                    # 可以进一步判断是否是依赖混儿胡的
                    pass  # 详细的计番逻辑会更复杂
                self.end_game(f"{player.name} {win_desc}胡了！", winner_id=player_id, winning_tile=win_desc)
                return {"success": True}
            else:
                logger.warning(
                    f"玩家 {player.name} 尝试自摸胡牌但服务器验证未通过。手牌: {player.hand}, 亮牌: {player.melds}")
                return {"success": False, "message": "当前不能胡牌"}

        elif action_type == "gang":  # 暗杠或补杠
            gang_type = action_data.get("gang_type")  # 'an' or 'bu'
            tile_info = action_data.get("tile_info")  # an: tile_str, bu: (meld_idx, tile_str)

            possible_an, possible_bu, _ = player.find_possible_gangs(game_rules=self.game_rules)
            is_valid_gang = False
            if gang_type == "an" and tile_info in possible_an:
                is_valid_gang = True
            elif gang_type == "bu" and tile_info in possible_bu:
                is_valid_gang = True

            if is_valid_gang and not player.is_listening:
                success = player.perform_gang(gang_type, tile_info, game_rules=self.game_rules)
                if success:
                    g_tile_display = tile_info if gang_type == 'an' else tile_info[1]
                    self.broadcast_message(
                        {"type": "player_ganged", "player_id": player.player_id, "tile": g_tile_display,
                         "gang_type": gang_type})
                    # 杠后摸牌并处理 (由 draw_and_handle_gang_replacement 完成)
                    self.draw_and_handle_gang_replacement(player)
                    return {"success": True}
                else:
                    return {"success": False, "message": "执行杠操作内部失败"}
            else:
                reason = "无效杠" if not is_valid_gang else "已叫听不能杠"
                logger.warning(f"玩家 {player.name} 尝试执行 {reason} (类型 {gang_type}, 信息 {tile_info})")
                return {"success": False, "message": reason}

        elif action_type == "ting":
            if player.is_listening:
                return {"success": False, "message": "已叫听"}
            # 叫听时手牌应为14张 (摸牌后，打牌前)
            if len(player.hand) != INITIAL_HAND_SIZE + 1:
                logger.error(f"玩家 {player.name} 尝试在手牌非{INITIAL_HAND_SIZE + 1}张({len(player.hand)})时叫听。")
                return {"success": False, "message": "手牌数错误无法叫听"}

            # 服务器验证是否真的能听 (至少打一张牌后能听)
            can_ting_server_check = False
            listening_options_after_discard = {}  # {discard_choice: [listening_tiles]}

            original_hand_for_ting_check = player.hand[:]
            for tile_to_try_discard in list(set(original_hand_for_ting_check)):  # 尝试打出每一种不同的牌
                player.hand = list(original_hand_for_ting_check)  # 恢复到14张
                player.remove_tile(tile_to_try_discard)  # 模拟打出一张，剩13张

                # 检查这13张牌听什么
                current_listens = player.find_listening_tiles(game_rules=self.game_rules,
                                                              possible_draw_tiles_list=self.all_game_tiles_list)
                if current_listens:
                    can_ting_server_check = True
                    listening_options_after_discard[tile_to_try_discard] = current_listens

            player.hand = original_hand_for_ting_check  # 恢复手牌到14张，等待客户端实际打牌

            if not can_ting_server_check:
                logger.info(f"玩家 {player.name} 请求叫听，但服务器验证打出任何牌后都无法听牌。")
                return {"success": False, "message": "当前手牌无法叫听"}

            # 实际的听牌状态设置和听牌列表的确认，应该在玩家选择打出牌之后。
            # 这里只是告诉客户端“你可以叫听了，请选择打哪张牌进入听牌状态”。
            # 服务器应该发送一个特殊的提示，让客户端选择打哪张牌来听。
            # 或者，简化规则：一旦声明听，就必须打出当前摸到的牌（如果听牌时是摸牌后14张）。
            # 当前的客户端逻辑是，如果叫听，自动打摸到的牌。

            # 简化：服务器接受听牌请求，客户端负责后续打牌。
            player.is_listening = True  # 先标记为听，如果后续打牌非法，由服务器处理
            # 此时 player.listening_tiles 还没有更新，会在打牌后确定
            logger.info(f"玩家 {player.name} 声明叫听。等待其打牌。")
            self.broadcast_message({"type": "player_tinged", "player_id": player_id})

            # 服务器应该重新提示玩家打牌，并且这张牌打出后，玩家的听牌列表会被确认。
            # 或者客户端直接处理：叫听后，打出牌，服务器确认该打法是否能听。
            # 我们采用后一种：客户端叫听后，会发送 discard，服务器在 discard 里验证。
            # 如果听牌后打出的牌不能构成听牌，则取消其听牌状态或报错。
            # player.listening_options = listening_options_after_discard # 可以暂存一下
            return {"success": True, "message": "已声明叫听，请打一张牌。"}  # 客户端会接着发discard

        else:
            logger.warning(f"收到来自玩家 {player.name} 的未知或不适用的行动类型: {action_type}")
            return {"success": False, "message": "未知行动"}

    def draw_and_handle_gang_replacement(self, player):
        if self.game_state != "playing": return False
        logger.info(f"{player.name} 杠后摸牌...")
        replacement_tile = self.deck.draw_from_end()
        if not replacement_tile:
            self.end_game("杠后无牌可摸 (流局)")
            return False

        player.add_tile(replacement_tile)
        logger.debug(f"{player.name} 杠后摸到: {replacement_tile} (手牌: {player.hand})")

        # 重置过水（自摸）状态，因为摸了新牌
        if self.game_rules.enable_passed_hu_rule:
            player.passed_hu_on_zimo_opportunity = False

        possible_an, possible_bu, _ = player.find_possible_gangs(game_rules=self.game_rules)
        actions = []
        if player.can_hu_tile(tile_to_win=replacement_tile, is_zimo=True, game_rules=self.game_rules):
            actions.append("hu")
        if not player.is_listening:
            if possible_an or possible_bu: actions.append("gang")
        if "hu" not in actions or len(player.hand) > 0:
            actions.append("discard")

        message = {
            "type": "action_prompt", "actions": actions, "drawn_tile": replacement_tile,
            "possible_an_gangs": possible_an, "possible_bu_gangs": possible_bu,
            "is_gang_replacement": True
        }
        self._next_prompt_info = (player.player_id, message)
        logger.debug(f"为玩家 {player.player_id} 设置杠后行动提示: {actions}")
        return True

    def check_other_players_actions(self):
        discarded_tile = self.last_discarded_tile
        discarder_id = self.last_discarder_id
        if not discarded_tile or discarder_id is None:
            self.advance_turn()
            return

        logger.debug(f"检查其他玩家对 玩家{discarder_id} 打出的 {discarded_tile} 的响应...")
        possible_actions_for_players = {}  # {player_id: [actions]}
        self.action_responses = {}
        action_found_for_any_player = False

        discarder_index = self.get_player_index_by_id(discarder_id)
        if discarder_index == -1:
            self.advance_turn();
            return

        for i in range(1, self.num_players):
            player_index = (discarder_index + i) % self.num_players
            player = self.players[player_index]
            player_actions_available = []
            player.can_hu_discard = False;
            player.can_gang = False;
            player.can_pong = False

            # 检查胡牌
            if player.can_hu_tile(tile_to_win=discarded_tile, is_zimo=False, game_rules=self.game_rules):
                player_actions_available.append("hu");
                player.can_hu_discard = True

            # 检查明杠 (听牌后不能杠)
            if not player.is_listening:
                _, _, possible_ming = player.find_possible_gangs(tile_from_discard=discarded_tile,
                                                                 game_rules=self.game_rules)
                if possible_ming: player_actions_available.append("gang"); player.can_gang = True

            # 检查碰牌 (听牌后不能碰)
            if not player.is_listening:
                if player.can_pong_tile(discarded_tile, game_rules=self.game_rules):
                    player_actions_available.append("pong");
                    player.can_pong = True

            if player_actions_available:
                possible_actions_for_players[player.player_id] = player_actions_available
                self.action_responses[player.player_id] = None  # 等待响应
                action_found_for_any_player = True
                logger.info(
                    f"玩家 {player.name} ({player.player_id}) 可以对 {discarded_tile} 执行: {player_actions_available}")

        if action_found_for_any_player:
            self.action_pending = True
            self._pending_action_info = {"type": "discard_response", "discarded_tile": discarded_tile,
                                         "discarder_id": discarder_id}
            logger.debug(f"设置 action_pending = True, 等待玩家 {list(possible_actions_for_players.keys())} 响应...")
            for p_id, actions_list in possible_actions_for_players.items():
                message = {"type": "action_prompt", "actions": actions_list, "tile": discarded_tile,
                           "discarder_id": discarder_id}
                self.send_message_to_player(p_id, message)
        else:
            logger.info("无人可响应弃牌，轮到下一家摸牌。")
            self.reset_action_state()
            self.advance_turn()

    def handle_action_response(self, player_id, response_data):
        if not self.action_pending or self._pending_action_info.get("type") != "discard_response":
            return {"success": False, "message": "当前无待处理操作"}
        if player_id not in self.action_responses or self.action_responses.get(player_id) is not None:
            return {"success": False, "message": "无需响应或已响应"}

        player = self.get_player_by_id(player_id)
        if not player: return {"success": False, "message": "玩家不存在"}

        response_type = response_data.get("action_type")
        discarded_tile_for_action = self._pending_action_info["discarded_tile"]

        allowed_server_side = ["pass"]
        if player.can_hu_discard: allowed_server_side.append("hu")
        if player.can_gang: allowed_server_side.append("gang")
        if player.can_pong: allowed_server_side.append("pong")

        if response_type not in allowed_server_side:
            logger.warning(
                f"玩家 {player.name} 发送了不允许的响应 '{response_type}' (允许: {allowed_server_side})，强制视为 Pass。")
            response_type = "pass"

        # 处理过水不胡
        if self.game_rules.enable_passed_hu_rule:
            if response_type == "pass" and player.can_hu_discard:  # 如果能胡但选择了过
                player.passed_hu_on_discard = discarded_tile_for_action
                logger.info(f"玩家 {player.name} 对牌 {discarded_tile_for_action} 选择过水不胡。")
            # 如果不是pass，且之前记录了过水，现在又选了胡，这个在 can_hu_tile 中已经判断过了
            # 如果玩家之前过水，现在选了碰或杠，则清除过水状态
            elif response_type in ["pong", "gang"]:
                player.passed_hu_on_discard = None

        self.action_responses[player_id] = response_type
        logger.info(f"玩家 {player.name} 响应对 {discarded_tile_for_action} 的操作: {response_type}")

        if all(response is not None for response in self.action_responses.values()):
            logger.debug("所有预期响应已收齐，开始进行裁决。")
            self.resolve_pending_actions()
        else:
            remaining_to_respond = [pid for pid, resp in self.action_responses.items() if resp is None]
            logger.debug(f"继续等待其他玩家响应: {remaining_to_respond}")
        return {"success": True}

    def resolve_pending_actions(self):
        if not self.action_pending: return

        discarded_tile_being_resolved = self._pending_action_info["discarded_tile"]
        discarder_id_of_tile = self._pending_action_info["discarder_id"]
        discarder_idx = self.get_player_index_by_id(discarder_id_of_tile)

        hu_player_id = None
        gang_player_obj = None
        pong_player_obj = None

        # 优先级：胡 > 杠 > 碰。同优先级按玩家顺序（离打牌者最近优先）
        for i in range(1, self.num_players):
            p_idx_check = (discarder_idx + i) % self.num_players
            p_obj_check = self.players[p_idx_check]
            p_id_check = p_obj_check.player_id
            response = self.action_responses.get(p_id_check)

            if response == "hu":
                hu_player_id = p_id_check;
                break  # 胡最优先
            if response == "gang" and gang_player_obj is None:
                gang_player_obj = p_obj_check
            if response == "pong" and pong_player_obj is None and gang_player_obj is None:  # 碰在杠之后
                pong_player_obj = p_obj_check

        action_was_taken = False
        if hu_player_id is not None:
            winner_obj = self.get_player_by_id(hu_player_id)
            self.end_game(f"{winner_obj.name} 接炮胡！", winner_id=hu_player_id,
                          winning_tile=discarded_tile_being_resolved)
            action_was_taken = True
        elif gang_player_obj is not None:
            logger.info(f"执行玩家 {gang_player_obj.name} 的明杠: {discarded_tile_being_resolved}")
            # 明杠时，tile_info 是杠的牌的种类，第三个参数是被杠的那张具体的牌
            success = gang_player_obj.perform_gang("ming", discarded_tile_being_resolved,
                                                   tile_discarded_for_ming_gang=discarded_tile_being_resolved,
                                                   game_rules=self.game_rules)
            if success:
                self.broadcast_message({"type": "player_ganged", "player_id": gang_player_obj.player_id,
                                        "tile": discarded_tile_being_resolved, "gang_type": "ming"})
                self.current_turn = self.get_player_index_by_id(gang_player_obj.player_id)  # 回合转移
                self.draw_and_handle_gang_replacement(gang_player_obj)
                action_was_taken = True
            else:
                logger.error(f"执行 {gang_player_obj.name} 的明杠时内部失败。")
        elif pong_player_obj is not None:
            logger.info(f"执行玩家 {pong_player_obj.name} 的碰: {discarded_tile_being_resolved}")
            success = pong_player_obj.perform_pong(discarded_tile_being_resolved)
            if success:
                self.broadcast_message({"type": "player_ponged", "player_id": pong_player_obj.player_id,
                                        "tile": discarded_tile_being_resolved})
                self.current_turn = self.get_player_index_by_id(pong_player_obj.player_id)  # 回合转移
                logger.info(f"轮到碰牌玩家 {pong_player_obj.name} ({pong_player_obj.player_id}) 出牌。")
                message = {"type": "action_prompt", "actions": ["discard"], "from_pong_gang": True}  # 碰后只能打牌
                self._next_prompt_info = (pong_player_obj.player_id, message)
                action_was_taken = True
            else:
                logger.error(f"执行 {pong_player_obj.name} 的碰时内部失败。")

        self.reset_action_state()
        if not action_was_taken:
            logger.info("所有玩家选择 Pass 或无有效操作，推进回合。")
            self.advance_turn()

    def reset_action_state(self):
        logger.debug("重置待处理行动状态 (action_pending, responses, flags)。")
        self.action_pending = False
        self.action_responses = {}
        self._pending_action_info = None
        for p in self.players:
            p.can_pong = False;
            p.can_gang = False;
            p.can_hu_discard = False

    def advance_turn(self):
        if self.game_state != "playing": return
        self.current_turn = (self.current_turn + 1) % self.num_players
        next_player = self.players[self.current_turn]
        logger.info(f"回合推进到 -> {next_player.name} ({next_player.player_id}).")
        if self.deck.remaining() == 0:
            self.end_game("牌摸完了 (流局)")
            return
        self.start_player_turn(self.current_turn)

    def end_game(self, reason, winner_id=None, winning_tile=None):
        if self.game_state == "finished":
            logger.warning(f"游戏已结束，忽略重复的结束请求 (原因: {reason})")
            return

        self.game_state = "finished"
        self.winning_player_id = winner_id
        self.winning_tile = winning_tile  # "自摸", "接炮的牌", "混儿自摸" etc.
        logger.info(f"--- 游戏结束！ 原因: {reason} ---")

        winner_name = "无"
        if winner_id is not None:
            winner = self.get_player_by_id(winner_id)
            if winner: winner_name = winner.name
            logger.info(f"获胜玩家: {winner_name} (ID: {winner_id})")
        else:
            logger.info("本局无胜者。")

        final_hands_info = {}
        for p in self.players:
            final_hands_info[str(p.player_id)] = {"hand": p.hand, "melds": p.melds}

        final_state_msg = {
            "type": "game_over", "reason": reason,
            "winning_player_id": winner_id, "winning_tile": winning_tile,
            "final_hands": final_hands_info,
            "joker_tile": self.game_rules.joker_tile
        }
        self.broadcast_message(final_state_msg)
        logger.info("游戏实例状态设置为 'finished'。")

    # --- 服务器绑定的方法占位符 ---
    def send_message_to_player(self, player_id, message):
        logger.debug(f"游戏占位符: 发送给玩家 {player_id}: 类型={message.get('type')}")
        pass

    def broadcast_message(self, message):
        logger.debug(f"游戏占位符: 广播消息: 类型={message.get('type')}")
        pass
