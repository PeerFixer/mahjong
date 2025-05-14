# mahjong_client.py
import json
import socket
import threading
import sys
import time
import logging
import traceback

from mahjong_common import send_json, receive_json, sort_tiles, tile_sort_key

SERVER_HOST = '127.0.0.1'
SERVER_PORT = 12345

logger = logging.getLogger(__name__)

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
    if not hand:
        return "[]"
    sorted_hand = sort_tiles(hand)
    display_parts = [f"{i + 1}:{translate_tile(tile)}" for i, tile in enumerate(sorted_hand)]
    return "  ".join(display_parts)


def format_meld_display(melds):
    if not melds: return "[]"
    return " ".join(["".join([translate_tile(t) for t in meld]) for meld in melds])


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
        self._action_thread = None

    def run(self):
        host = input(f"请输入服务器IP地址 (默认: {SERVER_HOST}): ") or SERVER_HOST
        port_str = input(f"请输入服务器端口 (默认: {SERVER_PORT}): ") or str(SERVER_PORT)
        try:
            port = int(port_str)
            logger.info(f"尝试连接到服务器 {host}:{port}...")
            self.client_socket.connect((host, port))
            print(f"成功连接到服务器 {host}:{port}")
            logger.info(f"成功连接到服务器 {host}:{port}")

            player_name_input = input("请输入你的玩家名称: ")
            self.player_name = player_name_input
            send_json(self.client_socket, {"type": "connect", "player_name": self.player_name})

            self._receive_thread = threading.Thread(target=self.receive_messages, name="ReceiveThread", daemon=True)
            self._receive_thread.start()

            self._action_thread = threading.Thread(target=self.send_actions_loop, name="ActionThread", daemon=True)
            self._action_thread.start()

            while not self._stop_event.is_set():
                if self._receive_thread and not self._receive_thread.is_alive():
                    logger.warning("接收线程意外停止。")
                    self._stop_event.set()
                if self._action_thread and not self._action_thread.is_alive():
                    logger.warning("行动线程意外停止。")
                    self._stop_event.set()
                time.sleep(0.5)

        except ConnectionRefusedError:
            print("连接被拒绝。请确认服务器已启动并且IP/端口正确。")
            logger.error("连接被服务器拒绝。")
        except ValueError:
            print("端口号无效。")
            logger.error(f"无效的端口号: {port_str}")
        except Exception as e:
            print(f"连接或运行过程中发生错误: {e}")
            logger.exception("客户端运行时发生错误")
        finally:
            logger.info("开始客户端关闭流程...")
            self.stop()
            time.sleep(0.2)  # 给线程一点时间响应事件
            if self.client_socket:
                try:
                    self.client_socket.shutdown(socket.SHUT_RDWR)
                except OSError:  # 可能socket已经关闭
                    pass
                try:
                    self.client_socket.close()
                except Exception:
                    pass
            print("客户端已关闭。")
            logger.info("客户端关闭完成。")

    def receive_messages(self):
        logger.info("接收线程已启动。")
        while not self._stop_event.is_set():
            try:
                message = receive_json(self.client_socket)
                if self._stop_event.is_set(): break
                if message is None:
                    logger.info("服务器连接已断开 (receive_json返回None)。")
                    self._stop_event.set()
                    break
                logger.debug(f"收到消息: {message}")
                self.handle_server_message(message)
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.exception("接收消息时发生错误")
                    self._stop_event.set()
                break
        logger.info("接收线程已退出。")

    def _handle_msg_connect_success(self, message):
        self.player_id = message.get("player_id")
        print(f"\n*** {message.get('message')} ***")
        logger.info(f"连接成功: ID={self.player_id}, Name={self.player_name}")

    def _handle_msg_player_joined(self, message):
        joined_player_id = message.get('player_id')
        joined_player_name = message.get('player_name')
        if joined_player_id != self.player_id:
            print(f"*** 玩家 {joined_player_name} ({joined_player_id}) 加入游戏。 ***")
        logger.info(f"玩家加入: ID={joined_player_id}, Name={joined_player_name}")

    def _handle_msg_game_state(self, message):
        logger.debug("收到游戏状态更新。")
        self._current_game_state = message.get("state")
        self.display_game_state()

    def _handle_msg_action_prompt(self, message):
        print("\n--- 收到行动提示 ---")
        logger.debug(f"收到行动提示: {message}")
        self._pending_action_prompt = message
        self.display_game_state()

    def _handle_player_event_log(self, message, event_type_str):
        player_id = message.get("player_id")
        player_name = self.get_player_name(player_id)
        tile = message.get("tile")
        translated_tile_str = f" ({translate_tile(tile)})" if tile else ""
        log_message = f"玩家 {player_name} ({player_id}) {event_type_str} {tile if tile else ''}{translated_tile_str}。"
        logger.info(log_message)
        if player_id != self.player_id:
            print(f"\n>> {log_message}")

    def _handle_msg_player_drew(self, message):
        # 通常客户端不需要知道其他玩家具体摸了什么牌，服务器也未发送具体牌信息
        player_id = message.get("player_id")
        player_name = self.get_player_name(player_id)
        logger.info(f"玩家 {player_name} ({player_id}) 摸牌。")

    def _handle_msg_player_discarded(self, message):
        self._handle_player_event_log(message, "打出")

    def _handle_msg_player_ponged(self, message):
        self._handle_player_event_log(message, "碰了")

    def _handle_msg_player_ganged(self, message):
        gang_type_display = {"an": "暗杠", "ming": "明杠", "bu": "补杠"}.get(message.get("gang_type", ""), "杠了")
        self._handle_player_event_log(message, gang_type_display)

    def _handle_msg_player_tinged(self, message):
        self._handle_player_event_log(message, "叫听")

    def _handle_msg_game_over(self, message):
        print("\n--- 游戏结束 ---")
        reason = message.get('reason')
        print(f"原因: {reason}")
        logger.info(f"游戏结束: {reason}")

        winner_id = message.get("winning_player_id")
        if winner_id is not None:
            winner_name = self.get_player_name(winner_id)
            print(f"获胜玩家: {winner_name}")
            logger.info(f"获胜玩家: {winner_name} ({winner_id})")
            winning_tile = message.get("winning_tile")
            if winning_tile and winning_tile != "自摸":  # "自摸" 是服务器传来的描述，不是牌
                print(f"胡的牌: {translate_tile(winning_tile)}")
            elif winning_tile == "自摸":
                print("自摸胡牌！")
        else:
            logger.info("本局无胜者。")

        final_hands = message.get("final_hands", {})
        print("\n--- 最终牌面 ---")
        logger.debug(f"最终牌面: {final_hands}")
        for pid_str, hand_info in final_hands.items():
            try:
                pid = int(pid_str)  # 服务器发送的 player_id 是整数，但在 JSON 键中是字符串
                player_name = self.get_player_name(pid)
                print(f"玩家 {player_name}:")
                print(f"  手牌: {format_hand_display(hand_info.get('hand', []))}")
                print(f"  亮牌: {format_meld_display(hand_info.get('melds', []))}")
                if hand_info.get('is_listening'):
                    print(f"  听牌: {format_discard_display(hand_info.get('listening_tiles', []))}")
            except ValueError:
                logger.error(f"无法解析最终牌面的玩家ID {pid_str}")
        print("--------------")

        self._current_game_state = None
        self._pending_action_prompt = None
        # 游戏结束后，客户端可以继续等待服务器消息（例如开始新游戏或断开连接）
        # 或者根据需要设置 self._stop_event.set() 来主动关闭

    def _handle_msg_error(self, message):
        error_msg = message.get('message', '未知错误')
        print(f"\n*** 服务器错误: {error_msg} ***")
        logger.error(f"收到服务器错误: {error_msg}")

    def _handle_unknown_message(self, message):
        logger.warning(f"收到未知消息类型: {message.get('type')}, 内容: {message}")

    def handle_server_message(self, message):
        msg_type = message.get("type")
        handler_name = f"_handle_msg_{msg_type}"
        handler = getattr(self, handler_name, self._handle_unknown_message)
        try:
            handler(message)
        except Exception as e:
            logger.exception(f"处理消息类型 {msg_type} 时发生错误: {message}")

    def display_game_state(self):
        state = self._current_game_state
        if not state:
            # print("\n等待游戏开始或状态更新...") # 可选的UI提示
            return

        print("\n" + "=" * 60)
        print(f"游戏状态: {state.get('game_state')}, 牌堆剩余: {state.get('wall_remaining')}")
        last_tile_str = translate_tile(state.get('last_discarded_tile')) if state.get('last_discarded_tile') else "无"
        last_discarder_name = self.get_player_name(state.get('last_discarder_id')) if state.get(
            'last_discarder_id') is not None else "未知"
        print(f"最后打出的牌: {last_tile_str} (由 {last_discarder_name})")
        print("-" * 60)

        player_states = sorted(state.get("players", []), key=lambda p: p.get('player_id', -1))

        for p_state in player_states:
            is_you = (p_state.get("player_id") == self.player_id)
            prefix = "*" if p_state.get("player_id") == state.get("current_turn_player_id") else " "
            name_str = f"{prefix}{p_state.get('name', f'玩家{p_state.get_player_id}')} ({p_state.get('player_id')})"
            status_parts = [f"手牌数: {p_state.get('hand_size', 0)}"]
            if p_state.get("is_listening"): status_parts.append("已叫听")

            print(f"{name_str} | {' | '.join(status_parts)}")
            print(f"  亮牌: {format_meld_display(p_state.get('melds'))}")
            print(f"  弃牌: {format_discard_display(p_state.get('discarded', []))}")
            if is_you:
                print(f"  你的手牌: {format_hand_display(state.get('your_hand', []))}")
                if p_state.get("is_listening"):
                    listening_tiles_display = format_discard_display(p_state.get('listening_tiles', []))
                    print(f"  你在听: {listening_tiles_display if listening_tiles_display else '无听张 (不应发生)'}")
        print("=" * 60)

        if self._pending_action_prompt:
            prompt = self._pending_action_prompt
            actions = prompt.get("actions", [])
            drawn_tile = prompt.get("drawn_tile")
            discard_tile_option = prompt.get("tile")  # 被响应的牌

            print("\n请选择你的行动:")
            if drawn_tile:
                print(f"  你摸到了: {translate_tile(drawn_tile)}")
            elif discard_tile_option:
                print(f"  对牌 {translate_tile(discard_tile_option)} 的响应:")

            print("  0. 重新展示手牌和提示")
            action_display_map = {"discard": "打牌", "hu": "胡牌", "pong": "碰", "gang": "杠", "ting": "听牌",
                                  "pass": "过"}

            my_p_state = next((p for p in player_states if p.get("player_id") == self.player_id), {})
            is_listening_now = my_p_state.get("is_listening", False)

            for i, action in enumerate(actions):
                action_text = action_display_map.get(action, action)
                if action == "discard" and drawn_tile and is_listening_now:
                    print(f"  {i + 1}. {action_text} (打出摸到的 {translate_tile(drawn_tile)})")
                else:
                    print(f"  {i + 1}. {action_text}")

            if "pass" not in actions:  # 如果服务器没给pass，我们提供一个标准的 "过"
                print(f"  {len(actions) + 1}. {action_display_map.get('pass', 'pass')}")
            print("---")

    def send_actions_loop(self):
        logger.info("行动处理线程已启动。")
        while not self._stop_event.is_set():
            if self._pending_action_prompt:
                try:
                    self.process_action_input()
                except Exception as e:
                    logger.exception("处理用户行动输入时发生错误")
                    # 发生错误时清除提示，避免循环错误处理
                    self._pending_action_prompt = None
            time.sleep(0.1)  # 避免忙等待
        logger.info("行动处理线程已退出。")

    def _prompt_for_discard_tile(self, hand_sorted, hand_size, drawn_tile_if_any, is_listening_player):
        """辅助函数：提示用户选择要打出的牌。"""
        while True:
            try:
                tile_idx_str = input(f"请输入你要打出的牌的序号 (1-{hand_size}): ").strip()
                if self._stop_event.is_set(): return None
                tile_idx = int(tile_idx_str) - 1
                if 0 <= tile_idx < hand_size:
                    candidate_tile = hand_sorted[tile_idx]
                    if is_listening_player and drawn_tile_if_any and candidate_tile != drawn_tile_if_any:
                        print(f"错误：叫听状态下，通常应打出摸到的牌 ({translate_tile(drawn_tile_if_any)})。")
                        # 实际服务器会处理听牌打牌逻辑，客户端主要是显示和采集
                        # 如果服务器严格要求，这里可以强制。目前允许玩家选择，服务器会验证。
                        # 如果选择继续，则返回选择的牌
                    return candidate_tile
                else:
                    print(f"序号无效，请输入 1 到 {hand_size} 之间的数字。")
            except ValueError:
                print("无效输入，请输入数字序号。")

    def _prompt_for_gang_choice(self, gang_options):
        """辅助函数：提示用户选择杠的类型和牌。"""
        print("选择要杠的牌:")
        for i, (g_type, g_info_display, _) in enumerate(gang_options):  # g_info_raw removed from here
            print(f"  {i + 1}. {'暗杠' if g_type == 'an' else '补杠'} {translate_tile(g_info_display)}")

        while True:
            gang_choice_input = input(f"请输入选择 (1-{len(gang_options)}): ").strip()
            if self._stop_event.is_set(): return None, None
            try:
                gang_idx = int(gang_choice_input) - 1
                if 0 <= gang_idx < len(gang_options):
                    chosen_g_type, _, chosen_g_info_raw = gang_options[gang_idx]
                    return chosen_g_type, chosen_g_info_raw
                else:
                    print("无效的选择。")
            except ValueError:
                print("无效输入。")

    def process_action_input(self):
        if not self._pending_action_prompt or not self._current_game_state:
            logger.warning("process_action_input 在没有提示或状态的情况下被调用。")
            return

        prompt = self._pending_action_prompt
        state = self._current_game_state
        server_actions = prompt.get("actions", [])  # 服务器提供的可选行动
        drawn_tile = prompt.get("drawn_tile")
        tile_to_respond_to = prompt.get("tile")  # 响应弃牌时的牌

        current_hand_sorted = sort_tiles(state.get("your_hand", []))
        hand_size = len(current_hand_sorted)

        print("请输入你的选择 (数字): ", end='', flush=True)
        user_input = input().strip()
        if self._stop_event.is_set(): return

        if not user_input.isdigit():
            print("无效输入，请输入数字。")
            return

        choice_num = int(user_input)
        if choice_num == 0:
            self.display_game_state()
            return

        chosen_action_str = None
        # 将用户输入的选项数字 (1-based) 转换为行动列表的索引 (0-based)
        action_idx = choice_num - 1

        if 0 <= action_idx < len(server_actions):
            chosen_action_str = server_actions[action_idx]
        elif "pass" not in server_actions and action_idx == len(server_actions):  # 如果服务器没给pass，我们客户端提供了一个
            chosen_action_str = "pass"
        else:
            # 检查是否是摸牌后，直接输入牌的序号来打牌 (服务器可能不直接将此作为"action"列出)
            # 这个逻辑主要用于简化打牌，但服务器的 "actions" 列表是权威的
            is_my_turn_draw_phase = drawn_tile and "discard" in server_actions  # 粗略判断是否是自己摸牌打牌阶段
            if is_my_turn_draw_phase and (1 <= choice_num <= hand_size):
                chosen_action_str = "discard"  # 意图是打牌
                # tile_to_discard 将在下面处理
            else:
                valid_options_count = len(server_actions) + (1 if "pass" not in server_actions else 0)
                print(f"无效选择。请输入 0-{valid_options_count} 之间的选项数字。")
                return

        action_message = None
        my_p_state = next((p for p in state.get("players", []) if p.get("player_id") == self.player_id), {})
        is_listening_now = my_p_state.get("is_listening", False)

        # --- 构建行动消息 ---
        if chosen_action_str == "discard":
            tile_to_discard = None
            if is_listening_now and drawn_tile:
                if drawn_tile in current_hand_sorted:
                    tile_to_discard = drawn_tile
                    print(f"已叫听，将打出摸到的牌: {translate_tile(tile_to_discard)}")
                else:  # 理论上不应发生
                    logger.error(f"听牌状态下摸到的牌 {drawn_tile} 不在手牌 {current_hand_sorted} 中!")
                    print("内部错误：听牌状态与手牌不符。请选择要打的牌。")
                    tile_to_discard = self._prompt_for_discard_tile(current_hand_sorted, hand_size, drawn_tile,
                                                                    is_listening_now)
            elif 1 <= choice_num <= hand_size and drawn_tile and "discard" in server_actions:  # 用户直接输入了牌的序号
                tile_to_discard = current_hand_sorted[choice_num - 1]
            else:  # 用户选择了 "打牌" 选项，或需要明确选择
                tile_to_discard = self._prompt_for_discard_tile(current_hand_sorted, hand_size, drawn_tile,
                                                                is_listening_now)

            if tile_to_discard:
                action_message = {"type": "action", "action_type": "discard", "tile": tile_to_discard}
                if drawn_tile: action_message["drawn_tile"] = drawn_tile  # 附带摸到的牌信息给服务器参考

        elif chosen_action_str == "hu":
            action_type_key = "action_response" if tile_to_respond_to else "action"
            action_message = {"type": action_type_key, "action_type": "hu"}

        elif chosen_action_str == "pong":
            if tile_to_respond_to:
                action_message = {"type": "action_response", "action_type": "pong"}
            else:
                print("错误：非响应状态不能碰牌。");
                return

        elif chosen_action_str == "gang":
            if tile_to_respond_to:  # 明杠别人的牌
                action_message = {"type": "action_response", "action_type": "gang"}  # 服务器会知道杠的是哪张牌
            else:  # 自己回合的暗杠或补杠
                possible_an_gangs = prompt.get("possible_an_gangs", [])  # tile string
                possible_bu_gangs_raw = prompt.get("possible_bu_gangs", [])  # (meld_index, tile_value)

                gang_options_for_prompt = []  # (type, display_tile, raw_info)
                for g_tile in possible_an_gangs: gang_options_for_prompt.append(("an", g_tile, g_tile))
                for meld_idx, tile_val in possible_bu_gangs_raw: gang_options_for_prompt.append(
                    ("bu", tile_val, (meld_idx, tile_val)))

                if not gang_options_for_prompt:
                    print("错误：服务器提示可杠但未找到有效杠牌选项。")
                    return
                if len(gang_options_for_prompt) == 1:
                    chosen_gang_type, display_tile, chosen_gang_info_raw = gang_options_for_prompt[0]
                    print(f"自动选择{'暗杠' if chosen_gang_type == 'an' else '补杠'}: {translate_tile(display_tile)}")
                else:
                    chosen_gang_type, chosen_gang_info_raw = self._prompt_for_gang_choice(gang_options_for_prompt)
                    if chosen_gang_type is None: return  # 用户中止或输入错误

                if chosen_gang_type:
                    action_message = {"type": "action", "action_type": "gang",
                                      "gang_type": chosen_gang_type, "tile_info": chosen_gang_info_raw}

        elif chosen_action_str == "ting":
            action_message = {"type": "action", "action_type": "ting"}

        elif chosen_action_str == "pass":
            if tile_to_respond_to:
                action_message = {"type": "action_response", "action_type": "pass"}
            else:  # 自己回合的 "pass" 通常是无效的，除非服务器有特殊处理
                print("自己回合不能 Pass，请选择有效操作或打出一张牌。")
                return  # 不发送，让用户重新选择

        # --- 发送行动消息 ---
        if action_message:
            logger.debug(f"准备发送行动: {action_message}")
            send_json(self.client_socket, action_message)
            logger.info(f"已发送行动: {action_message.get('action_type')}")
            self._pending_action_prompt = None  # 成功发送行动后，清除当前提示
        elif chosen_action_str:  # 如果选择了有效动作但未能构成消息 (例如杠牌时选择中止)
            logger.info(f"选择了行动 '{chosen_action_str}' 但未发送消息 (可能用户取消或输入不完整)。")
            # _pending_action_prompt 不清除，允许用户重新尝试当前提示
        # else: chosen_action_str 为 None (无效选择)，_pending_action_prompt 不清除

    def get_player_name(self, player_id_to_find):
        if player_id_to_find is None: return "未知"
        if self._current_game_state:
            player_info = next(
                (p for p in self._current_game_state.get("players", []) if p.get("player_id") == player_id_to_find),
                None)
            if player_info:
                return player_info.get("name", f"玩家 {player_id_to_find}")
        # 回退到客户端自身存储的名称（如果ID匹配）
        if self.player_id == player_id_to_find and self.player_name:
            return self.player_name
        return f"玩家 {player_id_to_find}"

    def stop(self):
        logger.info("设置停止事件...")
        self._stop_event.set()


# --- 主执行块 ---
if __name__ == "__main__":
    log_level = logging.INFO
    # log_level = logging.DEBUG # 取消注释以获取更详细的调试日志
    log_format = '%(asctime)s - %(levelname)-8s - [%(threadName)-10s] - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)

    client = MahjongClient()
    try:
        client.run()
    except KeyboardInterrupt:
        logger.info("收到Ctrl+C，正在关闭客户端...")
        client.stop()
        # 等待线程结束
        if client._receive_thread and client._receive_thread.is_alive():
            client._receive_thread.join(timeout=1)
        if client._action_thread and client._action_thread.is_alive():
            client._action_thread.join(timeout=1)
    except Exception as e:
        logger.critical("客户端主线程遇到未处理的异常", exc_info=True)
    finally:
        logger.info("客户端主程序退出。")
