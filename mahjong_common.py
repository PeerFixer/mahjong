# mahjong_common.py
# 麻将游戏通用工具和常量

import json
import socket
import struct
import logging

# 获取此模块的日志记录器
logger = logging.getLogger(__name__)

# --- 常量与牌定义 ---
# 牌的表示: 花色_点数 (例如: "wan_1", "feng_dong", "jian_zhong")
SUITS = ["wan", "tiao", "tong"] # 万, 条, 筒
POINTS = [str(i) for i in range(1, 10)] # 1-9
WINDS = ["dong", "nan", "xi", "bei"] # 东南西北风
DRAGONS = ["zhong", "fa", "bai"] # 中发白

# 所有可能的牌（按类型）
ALL_TILES_SUIT = [f"{suit}_{point}" for suit in SUITS for point in POINTS]
ALL_TILES_WIND = [f"feng_{wind}" for wind in WINDS]
ALL_TILES_DRAGON = [f"jian_{dragon}" for dragon in DRAGONS]

# 常量定义
TILES_PER_TYPE = 4      # 每种牌有4张
INITIAL_HAND_SIZE = 13 # 初始手牌数量
# --- 常量结束 ---

# --- 网络通信辅助函数 ---
def send_json(sock, data):
    """发送JSON数据，并在前面加上4字节的长度前缀（网络字节序）。"""
    try:
        logger.debug(f"准备发送数据类型: {data.get('type')}") # 使用调试级别记录日志
        data_bytes = json.dumps(data).encode('utf-8')
        # 使用 struct.pack 将长度打包为无符号长整型（大端字节序）
        length = struct.pack('>I', len(data_bytes))
        # 发送长度和数据
        sock.sendall(length + data_bytes)
    except Exception as e:
        # 记录包含异常信息的错误日志
        logger.exception("发送数据时发生错误 (send_json)")
        # 重新引发异常，让调用者处理（例如，断开连接）
        raise

def receive_json(sock):
    """接收带长度前缀的JSON数据。"""
    try:
        # 首先接收4字节的长度信息
        length_bytes = sock.recv(4)
        if not length_bytes:
            # 如果接收长度信息失败（例如，连接已关闭），则记录并返回 None
            logger.info("连接在接收长度前已关闭。")
            return None

        # 使用 struct.unpack 解包长度信息
        length = struct.unpack('>I', length_bytes)[0]

        # 对消息长度进行基本的健全性检查（例如，限制为合理大小）
        MAX_MSG_LENGTH = 1024 * 1024 # 设置1MB的上限，可根据需要调整
        if length > MAX_MSG_LENGTH:
             logger.error(f"接收到的消息长度过长: {length} > {MAX_MSG_LENGTH}")
             # 这里应该考虑如何处理：关闭连接或尝试恢复
             # 目前，返回 None 表示错误
             # 注意：可能需要消耗掉过长的消息数据以清理缓冲区
             # sock.recv(length) # 如果长度非常大，这可能有风险
             return None

        # 根据获取到的长度接收完整的数据
        data_bytes = b''
        while len(data_bytes) < length:
            # 循环接收，直到收到完整长度的数据
            packet = sock.recv(length - len(data_bytes))
            if not packet:
                # 如果在接收数据过程中连接意外关闭
                logger.warning("接收数据时连接意外关闭。")
                return None
            data_bytes += packet

        # 将接收到的字节解码为JSON对象
        data = json.loads(data_bytes.decode('utf-8'))
        logger.debug(f"成功接收并解析数据类型: {data.get('type')}") # 使用调试级别记录日志
        return data

    # --- 异常处理 ---
    except struct.error:
        logger.error("接收到无效的长度数据，可能连接异常。")
        return None
    except json.JSONDecodeError:
        logger.error("接收到无效的JSON数据。")
        return None
    except socket.timeout:
        logger.warning("接收数据超时。")
        return None
    except OSError as e:
        # 处理特定的操作系统错误，如管道破裂或连接重置
        logger.warning(f"接收数据时发生网络错误 (OSError): {e}")
        return None # 将其视为断开连接
    except Exception as e:
        # 记录其他未预料到的错误
        logger.exception("接收数据时发生未知错误 (receive_json)")
        # 这里不重新引发异常，返回 None 向调用者发出错误信号
        return None
# --- 网络函数结束 ---

# --- 牌排序与判断函数 ---
def tile_sort_key(tile):
    """为麻将牌定义排序键，用于 sorted() 函数。"""
    try:
        parts = tile.split('_')
        suite = parts[0]
        value = parts[1]
        # 定义花色和特殊牌的排序优先级
        suite_order = {"wan": 0, "tiao": 1, "tong": 2, "feng": 3, "jian": 4}
        order = suite_order.get(suite, 5) # 未知花色排在最后

        if suite in SUITS: # 万、条、筒 按点数排序
            point = int(value)
            return (order, point)
        elif suite == "feng": # 风牌按东、南、西、北排序
            wind_order = {"dong": 0, "nan": 1, "xi": 2, "bei": 3}
            return (order, wind_order.get(value, 4))
        elif suite == "jian": # 箭牌按中、发、白排序
            dragon_order = {"zhong": 0, "fa": 1, "bai": 2}
            return (order, dragon_order.get(value, 3))
        else: # 其他未知类型的牌
            return (order, 100)
    except (IndexError, ValueError):
         # 如果牌的格式不正确，给一个默认的高排序值
         logger.error(f"遇到无法解析的牌进行排序: {tile}")
         return (99, 99)


def sort_tiles(hand):
    """对手牌列表（字符串列表）进行排序。"""
    return sorted(hand, key=tile_sort_key)

def is_triplet(tiles):
    """检查牌列表是否是刻子 (AAA)。"""
    return len(tiles) == 3 and all(t == tiles[0] for t in tiles)

def is_quad(tiles):
    """检查牌列表是否是杠子 (AAAA)。"""
    return len(tiles) == 4 and all(t == tiles[0] for t in tiles)

def is_pair(tiles):
    """检查牌列表是否是对子 (AA)。"""
    return len(tiles) == 2 and all(t == tiles[0] for t in tiles)
# --- 排序与判断函数结束 ---