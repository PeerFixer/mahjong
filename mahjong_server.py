# mahjong_server.py
# 麻将游戏服务器主程序，处理连接、游戏循环和通信

import socket
import threading
import time
import json
import logging
import traceback
from mahjong_common import send_json, receive_json
from mahjong_game import Game, Player, GameRules # 确保 GameRules 被导入

# 服务器监听地址和端口
SERVER_HOST = '0.0.0.0'  # 监听所有可用网络接口
SERVER_PORT = 12345  # 监听端口

# 获取服务器主模块的日志记录器
logger = logging.getLogger(__name__)


class MahjongServer:
    """麻将服务器类，管理客户端连接和游戏实例。"""

    def __init__(self):
        """初始化服务器。"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.clients = {}
        self.players = [] # Player 对象列表
        self.game = None
        self._player_counter = 0
        self._lock = threading.Lock()
        self._game_instance_exists = False
        self._game_started_actual = False
        self._pending_client_input = None
        self._shutdown_requested = threading.Event()

    def run(self):
        """启动服务器，监听连接，并管理线程。"""
        try:
            self.server_socket.bind((SERVER_HOST, SERVER_PORT))
            self.server_socket.settimeout(1.0)
            self.server_socket.listen(5)
            logger.info(f"服务器在 {SERVER_HOST}:{SERVER_PORT} 监听...")

            game_thread = threading.Thread(target=self.game_loop, name="GameLoopThread", daemon=True)
            game_thread.start()
            logger.debug("游戏主循环线程已启动。")

            self.configure_game() # 配置游戏规则 (现在是硬编码)
            if not self._game_instance_exists:
                logger.error("游戏配置失败或被跳过，服务器即将退出。")
                self._shutdown_requested.set()
                return

            client_threads = []
            logger.info("开始接受客户端连接...")
            while not self._shutdown_requested.is_set():
                try:
                    conn, addr = self.server_socket.accept()
                    logger.info(f"接受来自 {addr} 的连接")
                    client_handler = threading.Thread(
                        target=self.handle_client,
                        args=(conn, addr),
                        name=f"ClientThread-{addr}",
                        daemon=True
                    )
                    client_handler.start()
                    client_threads.append(client_handler)
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self._shutdown_requested.is_set():
                        logger.exception("接受连接时发生错误")
                    break
            logger.info("服务器停止接受新连接。")

        except Exception as e:
            logger.exception("服务器运行时发生严重错误")
        finally:
            logger.info("开始服务器关闭流程...")
            self._shutdown_requested.set()
            if self.server_socket:
                try:
                    self.server_socket.close()
                    logger.info("服务器监听socket已关闭。")
                except Exception as e:
                    logger.error(f"关闭服务器监听socket时出错: {e}")
            logger.info("服务器关闭完成。")

    def configure_game(self):
        """
        配置游戏规则 (硬编码)。
        过水不胡: False
        混儿牌: None (无混儿)
        其他规则采用 GameRules 的默认值。
        """
        print("\n--- 正在配置游戏 (使用预设规则) ---")
        try:
            # 获取玩家人数
            while True:
                try:
                    num_players_str = input("请输入玩家人数 (2-4，默认4): ") or "4"
                    num_players = int(num_players_str)
                    if 2 <= num_players <= 4:
                        break
                    else:
                        print("人数必须在2到4之间。")
                except ValueError:
                    print("请输入有效的数字。")

            # 获取是否包含风牌和箭牌 (这个可以保留配置)
            while True:
                include_zh_fb_input = input("是否包含中发白和风牌 (y/n, 默认y): ").lower() or "y"
                if include_zh_fb_input in ['y', 'n']:
                    include_winds_dragons_config = (include_zh_fb_input == 'y')
                    break
                else:
                    print("请输入 'y' 或 'n'。")

            # 固定规则配置
            game_rules_config = {
                "enable_passed_hu_rule": False,  # 过水不胡: 关闭
                "joker_tile": None,             # 混儿牌: 无
                "allow_joker_pair": False,       # (由于无混儿，这些实际无效，但保持结构完整)
                "allow_joker_meld": False,
                "allow_joker_an_gang": False,
                "include_winds_dragons": include_winds_dragons_config # 这个从用户输入获取
            }
            logger.info(f"游戏规则已固定: 过水不胡=False, 混儿牌=None, 包含风箭牌={include_winds_dragons_config}")


            with self._lock:
                # 使用配置创建 Game 实例
                self.game = Game(num_players=num_players, game_rules_config=game_rules_config)
                self.game.send_message_to_player = self.send_message_to_player
                self.game.broadcast_message = self.broadcast_message
                self._game_instance_exists = True

            logger.info(
                f"游戏配置完成 ({num_players}人, 使用预设规则)。等待玩家加入...")

            try:
                hostname = socket.gethostname()
                try:
                    s_temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s_temp.settimeout(0.1)
                    s_temp.connect(('10.255.255.255', 1))
                    local_ip = s_temp.getsockname()[0]
                    s_temp.close()
                except Exception:
                    local_ip = socket.gethostbyname(hostname)
                print(f"提示: 客户端可连接到 {local_ip}:{SERVER_PORT} (同网络) 或 127.0.0.1:{SERVER_PORT} (本机)")
            except socket.gaierror:
                print(f"提示: 无法自动获取本机IP, 请客户端手动输入服务器IP或 127.0.0.1 (本机)")

        except Exception as e:
            logger.exception("配置游戏时发生错误")
            self._game_instance_exists = False


    def handle_client(self, conn, addr):
        """处理单个客户端连接：认证、消息接收和状态管理。"""
        player_id = None
        player_name_base = f"未知玩家@{addr}"
        player_added_successfully = False
        message_to_send_self = None
        message_to_broadcast = None

        try:
            logger.debug(f"等待来自 {addr} 的连接消息...")
            connect_message = receive_json(conn)
            if self._shutdown_requested.is_set():
                logger.info(f"服务器关闭中，忽略来自 {addr} 的消息。")
                return
            if connect_message is None:
                logger.info(f"连接 {addr} 在发送连接请求前已断开。")
                return

            if connect_message.get("type") != "connect":
                logger.warning(f"收到来自 {addr} 的无效连接请求 (非'connect'消息)。")
                message_to_send_self = {"type": "error", "message": "无效的连接请求"}
            else:
                player_name_input = connect_message.get("player_name", f"玩家_{self._player_counter}")
                player_name_base = player_name_input # 用于 Player 对象
                player_name_log = f"{player_name_input}@{addr[0]}" # 用于日志

                logger.debug(f"玩家 {player_name_log} 请求连接，获取锁...")
                with self._lock:
                    logger.debug(f"玩家 {player_name_log} 获取到锁。")
                    game_ready = self._game_instance_exists and not self._game_started_actual and self.game and self.game.game_state == "waiting"
                    can_add_player = False
                    if game_ready:
                        can_add_player = len(self.players) < self.game.num_players

                    if self._shutdown_requested.is_set():
                        message_to_send_self = {"type": "error", "message": "服务器正在关闭"}
                    elif not game_ready:
                        reason = "游戏已开始或服务器未就绪"
                        message_to_send_self = {"type": "error", "message": f"无法加入，{reason}。"}
                    elif not can_add_player:
                        message_to_send_self = {"type": "error", "message": "无法加入，玩家已满。"}
                    else:
                        player_id = self._player_counter
                        self._player_counter += 1
                        # 注意：Player 对象现在直接在这里创建和添加
                        player_obj = Player(player_id, player_name_base) # 使用不带IP的名称创建Player对象
                        self.clients[player_id] = conn
                        self.players.append(player_obj) # 添加Player对象到服务器的列表
                        if self.game: self.game.add_player(player_obj) # 添加Player对象到Game实例

                        player_added_successfully = True
                        current_player_count = len(self.players)
                        total_player_count = self.game.num_players if self.game else 0

                        message_to_send_self = {"type": "connect_success", "player_id": player_id,
                                                "player_name": player_name_base, # 发送给客户端的是不带IP的名称
                                                "message": f"欢迎 {player_name_base} 加入！({current_player_count}/{total_player_count})"}
                        message_to_broadcast = {"type": "player_joined", "player_id": player_id,
                                                "player_name": player_name_base} # 广播的也是不带IP的名称
                        logger.info(
                            f"玩家 {player_name_log} ({player_id}) 加入成功。({current_player_count}/{total_player_count})")
                logger.debug(f"玩家 {player_name_log} 释放锁。")

            if message_to_send_self:
                try:
                    logger.debug(f"准备发送响应给 {player_name_log if player_id is None else self.get_player_name_from_id_unsafe(player_id)} ({player_id}): 类型={message_to_send_self.get('type')}")
                    send_json(conn, message_to_send_self)
                    if message_to_send_self.get("type") == "error":
                        conn.close()
                        return
                except Exception as e:
                    logger.error(f"发送响应给新玩家 {player_name_log} ({player_id}) 时失败: {e}")
                    if player_id is not None: self.remove_player(player_id)
                    try: conn.close()
                    except Exception: pass
                    return

            if message_to_broadcast:
                self.broadcast_message(message_to_broadcast)

            if not player_added_successfully:
                return # 如果添加不成功，直接退出线程

            # 主接收循环
            logger.info(f"玩家 {self.get_player_name_from_id_unsafe(player_id)} ({player_id}) 进入主消息接收循环...")
            while not self._shutdown_requested.is_set():
                data = receive_json(conn)
                if self._shutdown_requested.is_set(): break
                if data is None:
                    logger.info(f"玩家 {self.get_player_name_from_id_unsafe(player_id)} ({player_id}) 连接断开。")
                    break

                input_queued = False
                queue_debug_msg = ""
                logger.debug(f"玩家 {player_id} 收到消息，尝试获取锁以放入队列...")
                with self._lock:
                    logger.debug(f"玩家 {player_id} 获取到锁。")
                    if self._shutdown_requested.is_set(): break

                    current_game_state_local = self.game.game_state if self.game else "no game"
                    if self._game_started_actual and current_game_state_local == "playing":
                        if data.get("type") in ["action", "action_response"]:
                            if self._pending_client_input is not None:
                                logger.warning(
                                    f"玩家 {player_id} 发送了输入，但上一个输入未处理。旧输入将被覆盖！")
                            self._pending_client_input = (data.get("type"), player_id, data)
                            input_queued = True
                            queue_debug_msg = f"DEBUG: 已将玩家 {player_id} 的输入 ({data.get('type')})放入待处理队列。"
                        else:
                            logger.warning(
                                f"收到玩家 {player_id} 在 playing 状态下的非预期消息类型: {data.get('type')}")
                    else:
                        logger.info(
                            f"收到玩家 {player_id} 在非 playing 状态下的消息 ({data.get('type')})，状态: {current_game_state_local}。消息被忽略。")
                logger.debug(f"玩家 {player_id} 释放锁。")
                if input_queued: logger.debug(queue_debug_msg)

        except (ConnectionResetError, BrokenPipeError, socket.error) as e:
            logger.warning(f"玩家 {player_name_base if player_id is None else self.get_player_name_from_id_unsafe(player_id)} ({player_id}) 连接中断: {e}")
        except Exception as e:
            logger.exception(f"处理玩家 {player_name_base if player_id is None else self.get_player_name_from_id_unsafe(player_id)} ({player_id}) 时发生未知错误")
        finally:
            logger.info(f"开始清理玩家 {player_name_base if player_id is None else self.get_player_name_from_id_unsafe(player_id)} ({player_id}) 的连接...")
            if player_id is not None:
                self.remove_player(player_id)
            else:
                try:
                    if conn: conn.close()
                except Exception: pass
            logger.info(f"客户端处理线程退出: {player_name_base if player_id is None else self.get_player_name_from_id_unsafe(player_id)} ({player_id})")


    def game_loop(self):
        """游戏主逻辑循环。"""
        logger.info("游戏主循环线程已启动。")
        while not self._shutdown_requested.is_set():
            action_to_process = None
            game_started_this_iteration = False
            game_ended_this_iteration = False
            prompt_to_send = None
            needs_broadcast = False
            current_game_state_snapshot = None

            logger.debug("GameLoop: 尝试获取锁...")
            with self._lock:
                logger.debug("GameLoop: 获取到锁。")
                if self._shutdown_requested.is_set(): break

                current_game_state_snapshot = self.game.game_state if self.game else "no game"
                logger.debug(
                    f"GameLoop: 当前状态={current_game_state_snapshot}, 实例存在={self._game_instance_exists}, 游戏已启动={self._game_started_actual}")

                if self._game_instance_exists and not self._game_started_actual and current_game_state_snapshot == "waiting":
                    if len(self.players) == self.game.num_players:
                        logger.info("GameLoop: 玩家数量已满，尝试启动游戏...")
                        try:
                            success = self.game.start_game() # start_game 现在使用 GameRules
                            if success:
                                self._game_started_actual = True
                                game_started_this_iteration = True
                                current_game_state_snapshot = "playing"
                                logger.info("GameLoop: 游戏启动成功，状态变为 playing")
                            else:
                                logger.error("GameLoop: 游戏启动失败 (Game.start_game 返回 False)。")
                        except Exception as e:
                            logger.exception("GameLoop: 调用 Game.start_game 时发生严重错误")
                            self._reset_server_state_internal()
                            game_ended_this_iteration = True

                if self._game_started_actual and current_game_state_snapshot == "playing" and self._pending_client_input:
                    action_to_process = self._pending_client_input
                    self._pending_client_input = None
                    logger.debug(
                        f"GameLoop: 获取到待处理输入: 类型={action_to_process[0]}, 玩家={action_to_process[1]}")

                if self._game_instance_exists and current_game_state_snapshot == "finished":
                    game_ended_this_iteration = True
                    logger.info("GameLoop: 检测到游戏结束状态，准备重置服务器...")
                    self._reset_server_state_internal()
            logger.debug("GameLoop: 释放锁。")

            if action_to_process and not game_ended_this_iteration:
                input_type, player_id, data = action_to_process
                player_name_log = self.get_player_name_from_id_unsafe(player_id)
                logger.debug(f"GameLoop: 开始处理玩家 {player_name_log}({player_id}) 的输入: 类型={input_type}")
                try:
                    if input_type == "action":
                        if self.game: self.game.handle_player_action(player_id, data)
                    elif input_type == "action_response":
                        if self.game: self.game.handle_action_response(player_id, data)
                    logger.debug(f"GameLoop: 处理玩家 {player_id} 输入完成。")
                except Exception as e:
                    logger.exception(f"GameLoop: 处理游戏输入 {input_type} (玩家 {player_id}) 时发生错误")
                    with self._lock:
                        if self.game and self.game.game_state == "playing":
                            self.game.end_game(f"服务器内部错误: {e}")
                        game_ended_this_iteration = True
                        current_game_state_snapshot = "finished" # 更新快照
                        self._reset_server_state_internal()


            if not game_ended_this_iteration:
                logger.debug("GameLoop: 检查是否需要广播或发送提示，尝试获取锁...")
                with self._lock:
                    logger.debug("GameLoop: 获取到锁。")
                    if self._shutdown_requested.is_set(): break

                    current_game_state_snapshot = self.game.game_state if self.game else "no game"
                    logger.debug(f"GameLoop: 检查时状态快照={current_game_state_snapshot}")

                    if self._game_started_actual and current_game_state_snapshot == "playing":
                        if game_started_this_iteration or action_to_process or (
                                self.game and self.game._next_prompt_info):
                            needs_broadcast = True
                            logger.debug("GameLoop: 标记需要广播状态。")

                        if self.game and self.game._next_prompt_info and not self.game.action_pending:
                            prompt_to_send = self.game._next_prompt_info
                            self.game._next_prompt_info = None
                            logger.debug(
                                f"GameLoop: 获取到待发送提示给玩家 {prompt_to_send[0]} 类型: {prompt_to_send[1].get('type')}")

                    if current_game_state_snapshot == "finished": # 再次检查是否结束
                        game_ended_this_iteration = True
                        logger.info("GameLoop: 检测到游戏结束状态 (处理后)，准备重置服务器...")
                        self._reset_server_state_internal()
                logger.debug("GameLoop: 释放锁。")


            if not game_ended_this_iteration:
                if needs_broadcast:
                    logger.debug("GameLoop: 开始广播游戏状态...")
                    self.broadcast_game_state()
                    logger.debug("GameLoop: 游戏状态广播完成。")

                if prompt_to_send:
                    p_id, message = prompt_to_send
                    logger.debug(f"GameLoop: 开始发送提示给玩家 {p_id}...")
                    self.send_message_to_player(p_id, message)
                    logger.debug("GameLoop: 提示发送完成。")
            try:
                sleep_duration = 0.1 if action_to_process or prompt_to_send or needs_broadcast else 0.2
                time.sleep(sleep_duration)
            except KeyboardInterrupt:
                logger.info("GameLoop: 收到 KeyboardInterrupt，设置关闭标志。")
                self._shutdown_requested.set()
                break
        logger.info("游戏主循环线程已退出。")


    def send_message_to_player(self, player_id, message):
        """向指定玩家发送消息。"""
        conn = None
        player_name = f"玩家 {player_id}"
        with self._lock:
            if self._shutdown_requested.is_set(): return False
            conn = self.clients.get(player_id)
            player_obj = self.get_player_by_id_internal(player_id)
            if player_obj: player_name = player_obj.name # 使用Player对象中的名称

        if conn:
            try:
                # 日志中使用不带IP的名称
                log_name = player_name.split('@')[0] if '@' in player_name else player_name
                logger.debug(f"SEND -> {log_name} ({player_id}): 类型={message.get('type')}")
                send_json(conn, message)
                return True
            except Exception as e:
                log_name_on_error = player_name.split('@')[0] if '@' in player_name else player_name
                logger.error(f"发送消息给玩家 {log_name_on_error} ({player_id}) 失败: {e}")
                self.remove_player(player_id)
                return False
        else:
            if player_id is not None:
                 log_name_if_no_conn = player_name.split('@')[0] if '@' in player_name else player_name
                 logger.warning(f"尝试发送消息给玩家 {log_name_if_no_conn} ({player_id})，但连接已不存在。")
            return False

    def broadcast_message(self, message):
        """向所有当前连接的客户端广播消息。"""
        disconnected_players = []
        clients_copy = {}
        with self._lock:
            if self._shutdown_requested.is_set(): return
            if not self.clients: return
            clients_copy = self.clients.copy()

        logger.debug(f"BROADCAST: 类型={message.get('type')} -> {len(clients_copy)} 个客户端。")

        for player_id, conn in clients_copy.items():
            player_name_for_log = self.get_player_name_from_id_unsafe(player_id) # 获取不带IP的名称
            message_to_send = message

            try:
                if message.get("type") == "game_state":
                    player_state = None
                    with self._lock: # 再次获取锁以安全访问 game 对象
                        if self._shutdown_requested.is_set(): continue
                        if self.game and self.game.game_state == "playing": # 确保游戏进行中
                            player_state = self.game.get_state_for_player(player_id)
                            # player_name_for_log 已在上面获取
                    if player_state:
                        message_to_send = {"type": "game_state", "state": player_state}
                    else:
                        logger.debug(f"跳过向玩家 {player_id} 广播 game_state (无法获取状态)。")
                        continue

                logger.debug(f"BROADCAST -> {player_name_for_log} ({player_id}): 类型={message_to_send.get('type')}")
                send_json(conn, message_to_send)
            except Exception as e:
                logger.error(f"广播消息给玩家 {player_name_for_log} ({player_id}) 失败: {e}")
                disconnected_players.append(player_id)

        if disconnected_players:
            logger.warning(f"广播后将移除断开连接的玩家: {disconnected_players}")
            for p_id in disconnected_players:
                self.remove_player(p_id)

    def broadcast_game_state(self):
        """广播当前游戏状态。"""
        should_broadcast = False
        with self._lock:
            if not self._shutdown_requested.is_set() and self._game_started_actual and self.game and self.game.game_state == "playing":
                should_broadcast = True
        if should_broadcast:
            self.broadcast_message({"type": "game_state", "state": "placeholder"}) # placeholder 会被定制替换

    def remove_player(self, player_id):
        """从服务器状态中移除玩家。"""
        if player_id is None: return
        player_name_log = f"玩家 {player_id}" # 默认日志名
        game_should_end_due_to_disconnect = False

        logger.debug(f"尝试移除玩家 {player_id}，获取锁...")
        with self._lock:
            logger.debug(f"移除玩家 {player_id} - 获取到锁。")
            if self._shutdown_requested.is_set():
                logger.info(f"忽略移除玩家 {player_id} 请求，服务器正在关闭。")
                return

            player_obj_to_remove = self.get_player_by_id_internal(player_id) # 获取 Player 对象
            if player_obj_to_remove:
                player_name_log = player_obj_to_remove.name # 使用 Player 对象中的名称
            else: #如果player对象列表中没有，尝试从clients字典获取连接时的名称（虽然不应该到这一步）
                pass


            if player_id in self.clients:
                logger.info(f"正在移除玩家 {player_name_log} ({player_id})...")
                conn = self.clients.pop(player_id, None)
                # 从 self.players (Player 对象列表) 移除
                original_player_count = len(self.players)
                self.players = [p for p in self.players if p.player_id != player_id]
                if len(self.players) < original_player_count:
                    logger.debug(f"已从服务器玩家对象列表 self.players 移除 {player_name_log}")

                if conn:
                    try: conn.shutdown(socket.SHUT_RDWR)
                    except Exception: pass
                    try: conn.close()
                    except Exception as e: logger.warning(f"关闭玩家 {player_id} socket时出错: {e}")

                if self.game:
                    if self._game_started_actual and self.game.game_state == "playing" and player_obj_to_remove:
                        logger.warning(f"玩家 {player_name_log} 在游戏进行中断开连接，将结束游戏。")
                        self.game.end_game(f"玩家 {player_name_log} 断开连接")
                        game_should_end_due_to_disconnect = True # 标记游戏应因此结束
                    elif not self._game_started_actual and player_obj_to_remove:
                         logger.info(f"玩家 {player_name_log} 在等待阶段断开连接。")
                         # 如果在Game对象中也有这个player，也需要移除
                         if self.game and player_obj_to_remove in self.game.players:
                             self.game.players.remove(player_obj_to_remove)
                             logger.debug(f"已从游戏实例的玩家列表移除 {player_name_log}")


            else:
                logger.warning(f"尝试移除玩家 {player_id}，但该玩家不在当前连接列表中。")
                return
        logger.debug(f"移除玩家 {player_id} - 释放锁。")
        # 游戏结束后的服务器状态重置由 game_loop 检测到 "finished" 状态后处理

    def _reset_server_state_internal(self):
        """重置服务器状态（必须在持有锁的情况下调用）。"""
        logger.warning("重置服务器内部状态 (持有锁)...")
        clients_to_close_sockets = list(self.clients.values()) # 获取socket对象列表
        if clients_to_close_sockets: logger.info(f"准备关闭 {len(clients_to_close_sockets)} 个剩余客户端连接...")

        self.clients = {}
        self.players = [] # 清空Player对象列表
        self._player_counter = 0 # 可以考虑是否重置ID计数器
        if self.game:
            # Game实例可能还包含玩家列表，如果Game实例的清理逻辑不完善，这里可以额外清一下
            # self.game.players = [] # 或者依赖 self.game = None
            pass
        self.game = None
        self._game_instance_exists = False
        self._game_started_actual = False
        self._pending_client_input = None

        for sock in clients_to_close_sockets:
            try: sock.shutdown(socket.SHUT_RDWR)
            except Exception: pass
            try: sock.close()
            except Exception: pass
        logger.warning("服务器状态已重置。游戏实例已清除。")
        # 提示用户服务器需要重新配置 (或自动重新配置)
        print("\n服务器已重置。如需开始新游戏，请重新运行服务器或实现重新配置逻辑。")
        # 当前实现下，服务器将无法再次启动游戏，因为 configure_game 只在开始时运行一次。
        # 可以选择在这里再次调用 self.configure_game() 来允许连续游戏，
        # 或者让服务器在run()方法结束后彻底退出。
        # 为简单起见，当前不自动重新配置。
        self._shutdown_requested.set() # 设置关闭，让主循环和game_loop退出


    def get_player_by_id_internal(self, player_id):
        """根据ID获取玩家对象（假定锁已被持有）。"""
        if player_id is None: return None
        for player in self.players: # self.players 现在是 Player 对象的列表
            if player.player_id == player_id:
                return player
        return None

    def get_player_name_from_id_unsafe(self, player_id):
        """获取玩家名称（主要用于锁外的日志记录）。"""
        if player_id is None: return "未知玩家"
        with self._lock: # 加锁保证线程安全
            player_obj = self.get_player_by_id_internal(player_id)
            if player_obj:
                return player_obj.name # 直接返回Player对象中的名称
            else:
                return f"玩家 {player_id}"


if __name__ == "__main__":
    log_level = logging.INFO
    # log_level = logging.DEBUG
    log_format = '%(asctime)s - %(levelname)-8s - %(name)-15s - %(threadName)-18s - %(message)s'
    logging.basicConfig(level=log_level, format=log_format)

    server = MahjongServer()
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("\n收到Ctrl+C，正在请求关闭服务器...")
        server._shutdown_requested.set()
        time.sleep(1) # 给其他线程一点时间响应关闭
    except Exception as e:
        logger.critical("服务器主线程遇到未处理的异常", exc_info=True)
    finally:
        logger.info("服务器主程序即将退出。")