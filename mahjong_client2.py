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
            # 记录此信息，除非打算作为状态显示给用户，否则不打印
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
            print(f"  亮牌: {format_meld_display(p_state.get('melds'))}")
            print(f"  弃牌: {format_discard_display(p_state.get('discarded', []))}")
            if is_you:
                print(f"  你的手牌: {format_hand_display(state.get('your_hand', []))}")
                if p_state.get("is_listening"):
                    print(f"  你在听: {format_discard_display(p_state.get('listening_tiles', []))}")

        print("=" * 50)

        # 使用 print 显示行动提示
        if self._pending_action_prompt:
            prompt = self._pending_action_prompt
            actions = prompt.get("actions", [])
            drawn_tile = prompt.get("drawn_tile")
            discard_tile_option = prompt.get("tile")
            print("\n请选择你的行动:")
            if drawn_tile:
                print(f"  你摸到了: {translate_tile(drawn_tile)}")
            elif discard_tile_option:
                print(f"  对牌 {translate_tile(discard_tile_option)} 的响应:")
            action_display_map = {"discard": "打牌", "hu": "胡牌", "pong": "碰", "gang": "杠", "ting": "听牌",
                                  "pass": "过"}
            for i, action in enumerate(actions):
                action_text = action_display_map.get(action, action)
                my_p_state = next((p for p in player_states if p.get("player_id") == self.player_id), None)
                is_listening_now = my_p_state.get("is_listening", False) if my_p_state else False
                if action == "discard" and drawn_tile and is_listening_now:
                    print(f"  {i + 1}. {action_text} (打出摸到的 {translate_tile(drawn_tile)})")
                else:
                    print(f"  {i + 1}. {action_text}")
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
        # 此代码已从 send_actions 移至由 send_actions_loop 调用的其自己的方法中
        if not self._pending_action_prompt or not self._current_game_state:
            logger.warning("process_action_input 在没有提示或状态的情况下被调用。")
            return  # 如果调用正确，则不应发生

        prompt = self._pending_action_prompt
        state = self._current_game_state
        actions = prompt.get("actions", [])
        drawn_tile = prompt.get("drawn_tile")
        discard_tile_option = prompt.get("tile")

        current_hand_sorted = sort_tiles(state.get("your_hand", []))
        hand_size = len(current_hand_sorted)

        # 使用 print 进行用户交互 (提示)
        print("请输入你的选择 (数字): ", end='', flush=True)  # flush 确保提示在输入前出现
        try:
            user_input = input().strip()  # 阻塞调用
            if self._stop_event.is_set(): return  # 检查输入期间是否停止

            chosen_action = None
            chosen_tile_index = -1

            if user_input.isdigit():
                choice_num = int(user_input)
                action_idx = choice_num - 1
                if 0 <= action_idx < len(actions):
                    chosen_action = actions[action_idx]
                elif action_idx == len(actions):
                    chosen_action = "pass"
                else:
                    if drawn_tile and "discard" in actions:
                        if 0 <= action_idx < hand_size:
                            chosen_action = "discard";
                            chosen_tile_index = action_idx
                        else:
                            print(
                                f"无效选择。请输入 1-{len(actions) + 1} 之间的选项数字，或 1-{hand_size} 之间的牌序号。"); return  # 返回循环
                    else:
                        print(f"无效选择。请输入 1-{len(actions) + 1} 之间的选项数字。"); return  # 返回循环
            else:
                # 允许输入操作名称？暂时保持简单，使用数字。
                print("无效输入，请输入数字。");
                return  # 返回循环

            # --- 构建行动消息 ---
            action_message = None
            my_p_state = next((p for p in state.get("players", []) if p.get("player_id") == self.player_id), None)
            is_listening_now = my_p_state.get("is_listening", False) if my_p_state else False

            if chosen_action == "discard":
                tile_to_discard = None
                if drawn_tile and is_listening_now:
                    if drawn_tile in current_hand_sorted:
                        tile_to_discard = drawn_tile
                        print(f"已叫听，自动打出摸到的牌: {translate_tile(tile_to_discard)}")  # UI 反馈
                    else:
                        logger.error(f"听牌状态下摸到的牌 {drawn_tile} 不在手牌 {current_hand_sorted} 中!")
                        print("内部错误：听牌状态与手牌不符，请选择要打的牌。")  # UI 反馈
                        # 强制用户在下面选择
                if tile_to_discard is None:
                    if chosen_tile_index != -1:
                        tile_to_discard = current_hand_sorted[chosen_tile_index]
                        # print(f"选择打出序号 {chosen_tile_index + 1}: {translate_tile(tile_to_discard)}") # UI 反馈
                    else:
                        # print(f"你的手牌: {format_hand_display(current_hand_sorted)}") # 再次显示？display_game_state 已经做过了。
                        while True:  # 获取有效索引的循环
                            try:
                                # 使用 print 处理 UI 提示
                                tile_idx_str = input(f"请输入你要打出的牌的序号 (1-{hand_size}): ").strip()
                                if self._stop_event.is_set(): return  # 在输入循环期间检查
                                tile_idx = int(tile_idx_str) - 1
                                if 0 <= tile_idx < hand_size:
                                    candidate_tile = current_hand_sorted[tile_idx]
                                    if drawn_tile and is_listening_now and candidate_tile != drawn_tile:
                                        print(
                                            f"错误：叫听状态下，只能打出摸到的牌 ({translate_tile(drawn_tile)})。请重新选择。")  # UI 反馈
                                        continue  # 再次询问
                                    tile_to_discard = candidate_tile
                                    break
                                else:
                                    print(f"序号无效，请输入 1 到 {hand_size} 之间的数字。")  # UI 反馈
                            except ValueError:
                                print("无效输入，请输入数字序号。")  # UI 反馈
                if tile_to_discard:  # 确保已选择一张牌
                    action_message = {"type": "action", "action_type": "discard", "tile": tile_to_discard,
                                      "drawn_tile": drawn_tile}

            elif chosen_action == "hu":
                if discard_tile_option:
                    action_message = {"type": "action_response", "action_type": "hu"}
                elif drawn_tile:
                    action_message = {"type": "action", "action_type": "hu"}

            elif chosen_action == "pong":
                if discard_tile_option: action_message = {"type": "action_response", "action_type": "pong"}

            elif chosen_action == "gang":
                if discard_tile_option:
                    action_message = {"type": "action_response", "action_type": "gang"}
                elif drawn_tile:
                    possible_an = prompt.get("possible_an_gangs", [])
                    possible_bu = prompt.get("possible_bu_gangs", [])
                    gang_options = []
                    for g_tile in possible_an: gang_options.append(("an", g_tile))
                    for meld_idx, tile in possible_bu: gang_options.append(("bu", (meld_idx, tile)))
                    if not gang_options: print("错误：服务器提示可杠但未找到选项。"); return  # UI 反馈
                    if len(gang_options) == 1:
                        chosen_gang_type, chosen_gang_info_raw = gang_options[0]
                        g_tile = chosen_gang_info_raw if chosen_gang_type == 'an' else chosen_gang_info_raw[1]
                        print(
                            f"自动选择{'暗杠' if chosen_gang_type == 'an' else '补杠'}: {translate_tile(g_tile)}")  # UI 反馈
                        action_message = {"type": "action", "action_type": "gang", "gang_type": chosen_gang_type,
                                          "tile_info": chosen_gang_info_raw}
                    else:
                        print("选择要杠的牌:")  # UI 提示
                        for i, (g_type, g_info) in enumerate(gang_options):
                            g_tile = g_info if g_type == 'an' else g_info[1]
                            print(
                                f"  {i + 1}. {'暗杠' if g_type == 'an' else '补杠'} {translate_tile(g_tile)}")  # UI 提示
                        while True:  # 杠牌选择循环
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
                                    print("无效的选择。")  # UI 反馈
                            except ValueError:
                                print("无效输入。")  # UI 反馈

            elif chosen_action == "ting":
                action_message = {"type": "action", "action_type": "ting"}

            elif chosen_action == "pass":
                if discard_tile_option:
                    action_message = {"type": "action_response", "action_type": "pass"}
                else:
                    # 向用户打印反馈，清除提示，不发送消息
                    print("自己回合不能 Pass，请选择有效操作。")  # UI 反馈
                    self._pending_action_prompt = None
                    action_message = None

            # --- 发送行动消息 ---
            if action_message:
                logger.debug(f"准备发送行动: {action_message}")
                send_json(self.client_socket, action_message)
                logger.info(f"已发送行动: {action_message.get('action_type')}")
                self._pending_action_prompt = None  # 仅在成功发送后清除提示
            # else: 如果行动未发送 (例如，无效的 pass)，则不清除提示 - 允许重新输入

        except ValueError:
            print("无效输入，请输入数字。")  # UI 反馈
            # 不清除提示，允许重试
        # 让其他异常传播到 run 方法的处理程序

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
