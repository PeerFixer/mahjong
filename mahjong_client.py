# mahjong_client.py
import json
import socket
import threading
import sys
import time
import logging  # 添加
import traceback  # 添加用于详细错误日志记录

from mahjong_common import send_json, receive_json, sort_tiles, tile_sort_key

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 12345

# 获取客户端的日志记录器
logger = logging.getLogger(__name__)  # 使用模块名称

# --- 牌张翻译和格式化函数 ---
TILE_TRANSLATION = {
    "wan_1": "一万", "wan_2": "二万", "wan_3": "三万", "wan_4": "四万", "wan_5": "五万",
    "wan_6": "六万", "wan_7": "七万", "wan_8": "八万", "wan_9": "九万",
    "tiao_1": "一条", "tiao_2": "二条", "tiao_3": "三条", "tiao_4": "四条", "tiao_5": "五条",
    "tiao_6": "六条", "tiao_7": "七条", "tiao_8": "八条", "tiao_9": "九条",
    "tong_1": "一筒", "tong_2": "二筒", "tong_3": "三筒", "tong_4": "四筒", "tong_5": "五筒",
    "tong_6": "六筒", "tong_7": "七筒", "tong_8": "八筒", "tong_9": "九筒",
    "feng_dong": "东风", "feng_nan": "南风", "feng_xi": "西风", "feng_bei": "北风",
    "jian_zhong": "红中", "jian_fa": "发财", "jian_bai": "白板",
}


def translate_tile(tile_str):
    return TILE_TRANSLATION.get(tile_str, tile_str)


# 从 mahjong_common 导入必要的函数 (假设它们在别处定义)
# from mahjong_common import sort_tiles
# # 假设 translate_tile 也在本文件中定义或导入
# TILE_TRANSLATION = { ... }
# def translate_tile(tile_str): ...

def format_hand_display(hand):
    """
    将手牌列表格式化为带序号和中文名称的单行字符串。
    例如： "1:一万  2:二万  3:三万 ..."
    """
    if not hand:
        return "[]"  # 如果手牌为空，返回 "[]"
    # 1. 对手牌进行排序
    sorted_hand = sort_tiles(hand)
    # 2. 为每张牌生成带序号和中文翻译的字符串部分
    #    例如: "1:一万", "2:九条", "3:东风" 等
    display_parts = [f"{i + 1}:{translate_tile(tile)}" for i, tile in enumerate(sorted_hand)]
    # 3. 使用 "  " (两个空格) 将所有部分连接成一个单行字符串
    formatted_str = "  ".join(display_parts)
    return formatted_str


def format_meld_display(melds):
    if not melds: return "[]"
    display_parts = []
    for meld in melds:
        translated_meld = [translate_tile(t) for t in meld]
        display_parts.append("".join(translated_meld))
    return " ".join(display_parts)


def format_discard_display(discards):
    return [translate_tile(t) for t in discards] if discards else []


# --- 结束牌张翻译 ---


class MahjongClient:
    def __init__(self):
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.player_id = None
        self.player_name = None
        self._stop_event = threading.Event()
        self._current_game_state = None
        self._pending_action_prompt = None
        self._receive_thread = None
        self._action_thread = None  # 用于管理行动线程关闭

    def run(self):
        """连接到服务器并启动线程。"""
        # 使用 print/input 进行初始用户交互
        host = input(f"请输入服务器IP地址 (默认: {SERVER_HOST}): ") or SERVER_HOST
        port_str = input(f"请输入服务器端口 (默认: {SERVER_PORT}): ") or str(SERVER_PORT)
        try:
            port = int(port_str)
            logger.info(f"尝试连接到服务器 {host}:{port}...")
            self.client_socket.connect((host, port))
            # 使用 print 直接向用户反馈成功信息
            print(f"成功连接到服务器 {host}:{port}")
            logger.info(f"成功连接到服务器 {host}:{port}")  # 也记录日志

            player_name = input("请输入你的玩家名称: ")
            self.player_name = player_name  # 尽早存储名称以便记录日志
            send_json(self.client_socket, {"type": "connect", "player_name": player_name})

            self._receive_thread = threading.Thread(target=self.receive_messages, name="ReceiveThread", daemon=True)
            self._receive_thread.start()

            # 在单独的线程中启动行动处理，因为 input() 是阻塞的
            self._action_thread = threading.Thread(target=self.send_actions_loop, name="ActionThread", daemon=True)
            self._action_thread.start()

            # 保持主线程活动，同时其他线程运行
            # 等待停止事件或线程结束
            while not self._stop_event.is_set():
                # 检查线程是否活动，如果意外停止则退出
                if not self._receive_thread.is_alive() or not self._action_thread.is_alive():
                    logger.warning("接收或行动线程已停止，设置停止事件。")
                    self._stop_event.set()
                time.sleep(0.5)  # 定期检查


        except ConnectionRefusedError:
            # 使用 print 直接向用户反馈
            print("连接被拒绝。请确认服务器已启动并且IP/端口正确。")
            logger.error("连接被服务器拒绝。")
        except ValueError:
            print("端口号无效。")
            logger.error(f"无效的端口号: {port_str}")
        except Exception as e:
            # 使用 print 直接向用户反馈
            print(f"连接或运行过程中发生错误: {e}")
            logger.exception("客户端运行时发生错误")
        finally:
            logger.info("开始客户端关闭流程...")
            self.stop()  # 确保设置停止事件
            # 短暂等待，让线程可能根据事件结束
            time.sleep(0.2)
            if self.client_socket:
                try:
                    self.client_socket.close()
                except Exception:
                    pass  # 关闭时忽略错误
            # 使用 print 向用户显示最终消息
            print("客户端关闭。")
            logger.info("客户端关闭完成。")

    def receive_messages(self):
        """从服务器接收消息。"""
        logger.info("接收线程已启动。")
        while not self._stop_event.is_set():
            try:
                message = receive_json(self.client_socket)
                if self._stop_event.is_set(): break  # 在阻塞调用后检查
                if message is None:
                    logger.info("服务器连接已断开 (receive_json返回None)。")
                    self._stop_event.set()  # 通知其他线程
                    break

                # 以调试级别记录收到的消息类型
                logger.debug(f"收到消息类型: {message.get('type')}")

                self.handle_server_message(message)

            except Exception as e:
                # 记录接收循环中发生的错误
                if not self._stop_event.is_set():  # 避免在关闭期间记录错误
                    logger.exception("接收消息时发生错误")
                    self._stop_event.set()  # 发生错误时通知其他线程
                break  # 发生错误时退出循环
        logger.info("接收线程已退出。")

    def handle_server_message(self, message):
        """处理从服务器收到的消息。"""
        msg_type = message.get("type")
        logger.debug(f"处理消息类型: {msg_type}")

        # 使用 print 处理直接的 UI 元素 (欢迎，加入 - 或许也记录日志)
        if msg_type == "connect_success":
            self.player_id = message.get("player_id")
            # self.player_name 已从输入设置
            # 直接向用户打印欢迎消息
            print(f"\n*** {message.get('message')} ***")
            logger.info(f"连接成功: ID={self.player_id}, Name={self.player_name}")

        elif msg_type == "player_joined":
            joined_player_id = message.get('player_id')
            joined_player_name = message.get('player_name')
            # 仅当是其他玩家加入时打印
            if joined_player_id != self.player_id:
                print(f"*** 玩家 {joined_player_name} ({joined_player_id}) 加入游戏。 ***")
            logger.info(f"玩家加入: ID={joined_player_id}, Name={joined_player_name}")


        elif msg_type == "game_state":
            logger.debug("收到游戏状态更新。")
            self._current_game_state = message.get("state")
            self.display_game_state()  # 保留 print 用于 UI

        elif msg_type == "action_prompt":
            # 使用 print 处理 UI 提示头部
            print("\n--- 收到行动提示 ---")
            logger.debug("收到行动提示。")
            self._pending_action_prompt = message
            # display_state 也使用 print 显示提示操作
            self.display_game_state()

        # 记录游戏事件，为其他玩家的行动打印消息
        elif msg_type == "player_drew":
            player_id = message.get("player_id")
            player_name = self.get_player_name(player_id)  # 使用辅助函数
            logger.info(f"玩家 {player_name} ({player_id}) 摸牌。")
            # 除非是相关的 UI 反馈，否则不向用户打印

        elif msg_type == "player_discarded":
            player_id = message.get("player_id")
            tile = message.get("tile")
            player_name = self.get_player_name(player_id)
            logger.info(f"玩家 {player_name} ({player_id}) 打出 {tile} ({translate_tile(tile)})。")
            # 如果是其他玩家，打印弃牌信息
            if player_id != self.player_id:
                print(f"\n>> 玩家 {player_name} 打出了 {translate_tile(tile)}。")

        elif msg_type in ["player_ponged", "player_ganged", "player_tinged"]:
            player_id = message.get("player_id")
            tile = message.get("tile", "")
            action_name = {"player_ponged": "碰", "player_ganged": "杠", "player_tinged": "叫听"}.get(msg_type,
                                                                                                      "未知操作")
            player_name = self.get_player_name(player_id)
            logger.info(f"玩家 {player_name} ({player_id}) 执行了 {action_name} {tile}")
            # 如果是其他玩家，打印行动信息
            if player_id != self.player_id:
                tile_display = translate_tile(tile) if tile else ""
                print(f"\n>> 玩家 {player_name} 执行了 {action_name} {tile_display}。")

        elif msg_type == "game_over":
            # 使用 print 处理游戏结束消息和最终手牌 (UI)
            print("\n--- 游戏结束 ---")
            print(f"原因: {message.get('reason')}")
            logger.info(f"游戏结束: {message.get('reason')}")
            winner_id = message.get("winning_player_id")
            if winner_id is not None:
                winner_name = self.get_player_name(winner_id)
                print(f"获胜玩家: {winner_name}")
                logger.info(f"获胜玩家: {winner_name} ({winner_id})")
                winning_tile = message.get("winning_tile")
                if winning_tile and winning_tile != "自摸":
                    print(f"胡的牌: {translate_tile(winning_tile)}")
                elif winning_tile == "自摸":
                    print("自摸胡牌！")
            else:
                logger.info("本局无胜者。")

            final_hands = message.get("final_hands", {})
            print("\n--- 最终牌面 ---")
            # 以调试级别记录最终手牌
            logger.debug(f"最终牌面: {final_hands}")
            for pid_str, hand_info in final_hands.items():
                try:
                    pid = int(pid_str)
                    player_name = self.get_player_name(pid) or f"Player {pid}"
                    print(f"玩家 {player_name}:")
                    print(f"  手牌: {format_hand_display(hand_info.get('hand', []))}")
                    print(f"  亮牌: {format_meld_display(hand_info.get('melds', []))}")
                except ValueError:
                    logger.error(f"无法解析最终牌面的玩家ID {pid_str}")
            print("--------------")

            self._current_game_state = None
            self._pending_action_prompt = None
            # 发出停止信号？还是等待服务器断开/新游戏提示？
            # 假设服务器断开或用户关闭客户端。
            # self._stop_event.set() # 可选：游戏结束后停止客户端

        elif msg_type == "error":
            error_msg = message.get('message', '未知错误')
            # 向用户打印服务器错误
            print(f"\n*** 服务器错误: {error_msg} ***")
            logger.error(f"收到服务器错误: {error_msg}")

        else:
            logger.warning(f"收到未知消息类型: {msg_type}")

    def display_game_state(self):
        """使用 print 显示游戏状态以用于 UI。"""
        state = self._current_game_state
        if not state:
            logger.debug("display_game_state 被调用但 state 为 None。")
            # print("\n等待游戏开始或状态更新...") # 如果这是期望的 UI，则保留 print
            return

        # 使用 print 处理所有打算给用户看的游戏状态显示
        print("\n" + "=" * 50)
        print(f"游戏状态: {state.get('game_state')}")
        print(f"牌堆剩余: {state.get('wall_remaining')}")
        last_tile_str = translate_tile(state.get('last_discarded_tile')) if state.get('last_discarded_tile') else "无"
        last_discarder_name = self.get_player_name(state.get('last_discarder_id')) if state.get(
            'last_discarder_id') is not None else "未知"
        print(f"最后打出的牌: {last_tile_str} (由 {last_discarder_name})")
        print("-" * 50)

        player_states = sorted(state.get("players", []), key=lambda p: p.get('player_id', 0))

        for p_state in player_states:
            is_you = (p_state.get("player_id") == self.player_id)
            prefix = "*" if p_state.get("player_id") == state.get("current_turn_player_id") else " "
            name_str = f"{prefix}{p_state.get('name')} ({p_state.get('player_id')})"
            status_str = f" | 手牌数: {p_state.get('hand_size', 0)}"
            if p_state.get("is_listening"): status_str += " | 已叫听"
            print(name_str + status_str)
            # 假设 format_meld_display 和 format_hand_display 已按之前的要求修改为单行或您期望的格式
            print(f"  亮牌: {format_meld_display(p_state.get('melds'))}")
            print(f"  弃牌: {format_discard_display(p_state.get('discarded', []))}")
            if is_you:
                print(f"  你的手牌: {format_hand_display(state.get('your_hand', []))}")  # 使用单行手牌显示
                if p_state.get("is_listening"):
                    print(f"  你在听: {format_discard_display(p_state.get('listening_tiles', []))}")

        print("=" * 50)

        # 使用 print 显示行动提示
        if self._pending_action_prompt:
            prompt = self._pending_action_prompt
            actions = prompt.get("actions", [])  # 服务器提供的可选行动
            drawn_tile = prompt.get("drawn_tile")  # 如果是自己摸牌回合，摸到的牌
            discard_tile_option = prompt.get("tile")  # 如果是响应他人弃牌，被弃的牌

            print("\n请选择你的行动:")
            if drawn_tile:
                print(f"  你摸到了: {translate_tile(drawn_tile)}")
            elif discard_tile_option:
                print(f"  对牌 {translate_tile(discard_tile_option)} 的响应:")

            # <<< 新增的选项 >>>
            print(f"  0. 重新展示手牌和摸到的牌")

            action_display_map = {"discard": "打牌", "hu": "胡牌", "pong": "碰", "gang": "杠", "ting": "听牌",
                                  "pass": "过"}
            for i, action in enumerate(actions):
                action_text = action_display_map.get(action, action)
                # 如果是自己回合，已听牌，且行动是打牌，则特殊提示打出摸到的牌
                my_p_state = next((p for p in player_states if p.get("player_id") == self.player_id), None)
                is_listening_now = my_p_state.get("is_listening", False) if my_p_state else False
                if action == "discard" and drawn_tile and is_listening_now:
                    print(f"  {i + 1}. {action_text} (打出摸到的 {translate_tile(drawn_tile)})")
                else:
                    print(f"  {i + 1}. {action_text}")
            # "过" 选项总是最后，序号是 len(actions) + 1
            print(f"  {len(actions) + 1}. {action_display_map.get('pass', 'pass')}")
            print("---")

    def send_actions_loop(self):
        """等待提示并处理用户输入的循环。"""
        logger.info("行动处理线程已启动。")
        while not self._stop_event.is_set():
            # 等到提示可用
            if self._pending_action_prompt:
                try:
                    self.process_action_input()  # 调用分离的逻辑
                except Exception as e:
                    # 记录行动处理过程中的错误
                    logger.exception("处理用户行动输入时发生错误")
                    # 可选：发出停止信号？取决于严重性。
                    # self._stop_event.set()
                    # 发生错误时清除提示以避免重复处理？
                    self._pending_action_prompt = None
            # 即使没有提示也休眠，以避免忙等待
            time.sleep(0.1)
        logger.info("行动处理线程已退出。")

    def process_action_input(self):
        """根据提示处理获取和发送玩家行动的逻辑。"""
        if not self._pending_action_prompt or not self._current_game_state:
            logger.warning("process_action_input 在没有提示或状态的情况下被调用。")
            return

        prompt = self._pending_action_prompt
        state = self._current_game_state
        actions = prompt.get("actions", [])
        drawn_tile = prompt.get("drawn_tile")
        discard_tile_option = prompt.get("tile")

        current_hand_sorted = sort_tiles(state.get("your_hand", []))
        hand_size = len(current_hand_sorted)

        print("请输入你的选择 (数字): ", end='', flush=True)
        try:
            user_input = input().strip()
            if self._stop_event.is_set(): return

            chosen_action = None
            chosen_tile_index = -1  # 用于直接通过数字选择打哪张牌

            if not user_input.isdigit():
                print("无效输入，请输入数字。")
                return  # 返回到 send_actions_loop，再次提示 (因为 _pending_action_prompt 未清除)

            choice_num = int(user_input)

            # <<< 处理新增的 "0" 选项 >>>
            if choice_num == 0:
                self.display_game_state()  # 重新显示游戏状态、手牌和行动提示
                return  # 直接返回，不发送消息给服务器，_pending_action_prompt 保持不变，以便再次提示用户输入

            # 将用户输入的选项数字 (1-based) 转换为行动列表的索引 (0-based)
            action_idx = choice_num - 1

            if 0 <= action_idx < len(actions):
                # 用户选择了一个服务器提供的标准行动
                chosen_action = actions[action_idx]
            elif action_idx == len(actions):
                # 用户选择了列表末尾的 "过" (pass) 选项
                chosen_action = "pass"
            else:
                # 用户输入的数字不在标准选项 [1, len(actions)+1] 范围内
                # 检查是否是摸牌后，直接输入牌的序号来打牌
                if drawn_tile and "discard" in actions and (1 <= choice_num <= hand_size):
                    # 条件: 是自己摸牌的回合 (有drawn_tile)，服务器允许打牌 ("discard" in actions)，
                    # 且输入的数字是手牌的有效序号 (1 到 hand_size)
                    chosen_action = "discard"
                    chosen_tile_index = choice_num - 1  # 将1-based的牌序号转为0-based的列表索引
                else:
                    # 其他情况视为无效选择
                    valid_options_range = f"0-{len(actions) + 1}"
                    hand_range_info = ""
                    if drawn_tile and "discard" in actions:
                        hand_range_info = f"或在打牌时输入 1-{hand_size} 之间的牌序号"
                    print(f"无效选择。请输入 {valid_options_range} 之间的选项数字{hand_range_info}。")
                    return  # 返回到 send_actions_loop，再次提示

            # --- 构建行动消息 ---
            action_message = None
            my_p_state = next((p for p in state.get("players", []) if p.get("player_id") == self.player_id), None)
            is_listening_now = my_p_state.get("is_listening", False) if my_p_state else False

            if chosen_action == "discard":
                tile_to_discard = None
                if drawn_tile and is_listening_now:  # 如果已听牌，则自动打出摸到的牌
                    if drawn_tile in current_hand_sorted:  # 理论上应该在
                        tile_to_discard = drawn_tile
                        print(f"已叫听，自动打出摸到的牌: {translate_tile(tile_to_discard)}")
                    else:  # 容错：如果摸到的牌不在手里（不应发生）
                        logger.error(f"听牌状态下摸到的牌 {drawn_tile} 不在手牌 {current_hand_sorted} 中!")
                        print("内部错误：听牌状态与手牌不符。请选择要打的牌。")
                        # 此时 tile_to_discard 仍为 None，会进入下面的手动选择逻辑

                if tile_to_discard is None:  # 如果不是听牌自动打，或者需要手动选择
                    if chosen_tile_index != -1:  # 用户通过输入牌的序号直接选择了要打的牌
                        tile_to_discard = current_hand_sorted[chosen_tile_index]
                    else:  # 用户选择了 "打牌" 这个动作，现在需要询问具体打哪一张
                        while True:  # 循环直到获得有效输入
                            try:
                                tile_idx_str = input(f"请输入你要打出的牌的序号 (1-{hand_size}): ").strip()
                                if self._stop_event.is_set(): return
                                tile_idx = int(tile_idx_str) - 1
                                if 0 <= tile_idx < hand_size:
                                    candidate_tile = current_hand_sorted[tile_idx]
                                    # 如果听牌，只能打摸到的牌
                                    if drawn_tile and is_listening_now and candidate_tile != drawn_tile:
                                        print(
                                            f"错误：叫听状态下，只能打出摸到的牌 ({translate_tile(drawn_tile)})。请重新选择。")
                                        continue
                                    tile_to_discard = candidate_tile
                                    break
                                else:
                                    print(f"序号无效，请输入 1 到 {hand_size} 之间的数字。")
                            except ValueError:
                                print("无效输入，请输入数字序号。")

                if tile_to_discard:  # 确保最终选出了一张牌
                    action_message = {"type": "action", "action_type": "discard", "tile": tile_to_discard,
                                      "drawn_tile": drawn_tile}

            elif chosen_action == "hu":
                if discard_tile_option:  # 是响应别人的弃牌胡
                    action_message = {"type": "action_response", "action_type": "hu"}
                elif drawn_tile:  # 是自己摸牌后自摸
                    action_message = {"type": "action", "action_type": "hu"}

            elif chosen_action == "pong":
                if discard_tile_option:  # 碰只能是响应别人的弃牌
                    action_message = {"type": "action_response", "action_type": "pong"}
                # else: 自己摸牌回合不能碰，chosen_action 不会是pong

            elif chosen_action == "gang":
                if discard_tile_option:  # 响应别人的弃牌形成明杠
                    action_message = {"type": "action_response", "action_type": "gang"}
                elif drawn_tile:  # 自己摸牌后杠（暗杠或加杠/补杠）
                    possible_an_gangs = prompt.get("possible_an_gangs", [])
                    possible_bu_gangs = prompt.get("possible_bu_gangs", [])  # 格式: [(meld_index, tile), ...]
                    gang_options = []
                    for g_tile in possible_an_gangs: gang_options.append(("an", g_tile))
                    for meld_idx, tile_val in possible_bu_gangs: gang_options.append(("bu", (meld_idx, tile_val)))

                    if not gang_options:
                        print("错误：服务器提示可杠但未找到有效杠牌选项。")
                        return  # 让用户重新输入或等待服务器修正

                    if len(gang_options) == 1:  # 只有一个杠的选项，自动选择
                        chosen_gang_type, chosen_gang_info_raw = gang_options[0]
                        g_display_tile = chosen_gang_info_raw if chosen_gang_type == 'an' else chosen_gang_info_raw[1]
                        print(
                            f"自动选择{'暗杠' if chosen_gang_type == 'an' else '补杠'}: {translate_tile(g_display_tile)}")
                        action_message = {"type": "action", "action_type": "gang", "gang_type": chosen_gang_type,
                                          "tile_info": chosen_gang_info_raw}
                    else:  # 有多个杠的选项，让用户选择
                        print("选择要杠的牌:")
                        for i, (g_type, g_info) in enumerate(gang_options):
                            g_display_tile_multi = g_info if g_type == 'an' else g_info[1]
                            print(
                                f"  {i + 1}. {'暗杠' if g_type == 'an' else '补杠'} {translate_tile(g_display_tile_multi)}")
                        while True:  # 循环直到获得有效杠牌选择
                            gang_choice_input = input(f"请输入选择 (1-{len(gang_options)}): ").strip()
                            if self._stop_event.is_set(): return
                            try:
                                gang_idx = int(gang_choice_input) - 1
                                if 0 <= gang_idx < len(gang_options):
                                    chosen_gang_type, chosen_gang_info_raw = gang_options[gang_idx]
                                    action_message = {"type": "action", "action_type": "gang",
                                                      "gang_type": chosen_gang_type, "tile_info": chosen_gang_info_raw}
                                    break
                                else:
                                    print("无效的选择。")
                            except ValueError:
                                print("无效输入。")

            elif chosen_action == "ting":
                action_message = {"type": "action", "action_type": "ting"}

            elif chosen_action == "pass":
                if discard_tile_option:  # 响应弃牌时选择 "过" 是合法的
                    action_message = {"type": "action_response", "action_type": "pass"}
                else:  # 自己摸牌的回合，通常不能 "过" (除非服务器逻辑允许，但客户端这里阻止)
                    print("自己回合不能 Pass，请选择有效操作。")
                    return  # 返回，让用户重新输入 (因为 _pending_action_prompt 未清除)

            # --- 发送行动消息 ---
            if action_message:
                logger.debug(f"准备发送行动: {action_message}")
                send_json(self.client_socket, action_message)
                logger.info(f"已发送行动: {action_message.get('action_type')}")
                self._pending_action_prompt = None  # 成功发送行动后，清除当前提示，等待服务器新状态/提示
            # else:
            # 如果 action_message 为 None (例如，因为选了0，或者选了无效的自己回合pass，或者杠牌时未找到选项然后return了),
            # 并且 _pending_action_prompt 没有被上面的逻辑清除，
            # send_actions_loop 会继续使用旧的 _pending_action_prompt 来调用 process_action_input，
            # 这会导致重新提示用户输入。这是期望的行为。

        except ValueError:  # int(user_input) 转换失败
            print("无效输入，请输入数字。")
            # 不清除 _pending_action_prompt，允许用户在下一次循环中重试

    def get_player_name(self, player_id):
        """获取玩家名称，首先尝试当前状态。"""
        if player_id is None: return "未知"
        if self._current_game_state:
            for p_state in self._current_game_state.get("players", []):
                if p_state.get("player_id") == player_id:
                    return p_state.get("name", f"玩家 {player_id}")
        # 早期状态或日志记录的回退
        # 如果 ID 匹配，则使用 self.player_name，否则使用默认值
        if hasattr(self, 'player_id') and self.player_id == player_id and hasattr(self,
                                                                                  'player_name') and self.player_name:
            return self.player_name
        return f"玩家 {player_id}"

    def stop(self):
        """通知线程停止。"""
        logger.info("设置停止事件...")
        self._stop_event.set()


# --- 主执行块 ---
if __name__ == "__main__":
    # --- 配置日志 ---
    log_level = logging.INFO  # 默认级别
    # # 取消注释以获取更详细的调试日志：
    # log_level = logging.DEBUG

    # 客户端控制台的简单格式
    log_format = '%(asctime)s - %(levelname)-8s - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)
    # --- 结束日志配置 ---

    client = MahjongClient()
    try:
        client.run()
    except KeyboardInterrupt:
        logger.info("收到Ctrl+C，正在关闭客户端...")
        client.stop()
    except Exception as e:
        logger.critical("客户端主线程遇到未处理的异常", exc_info=True)
    finally:
        # run 方法处理最终的日志记录和关闭
        pass
