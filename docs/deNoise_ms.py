# %% ==================== 程序说明 ====================
"""
一、程序功能概述
1. 主要为质谱数据（MS或LC-MS/GC-MS）选择性去噪工具包，专门处理时间序列型质谱数据（每列代表一个 m/z 特征，每行代表一个扫描时间点）。
其核心目标是针对不同 m/z 特征（列）自动或手动选用最合适的去噪算法，以提高后续定量或定性分析的准确性。
2.主要功能模块包括：
(1) 去噪方法库（DenoiseMethod 及其子类）
..实现了 8 种常用去噪算法：滑动平均、标准差阈值、指数加权移动平均（EWMA）、滑动中值、分位数滤波、Savitzky‑Golay 滤波、小波去噪（Wavelet）、
   卡尔曼滤波、自研技术（可扩展），以及“不处理”选项。
(2) 配置管理（DenoiseConfigManager）
..为每一列单独指定去噪方法和超参数。
..支持按 m/z 范围、平均信号强度 批量设置配置。
..配置可保存为 JSON 文件，并支持重新加载，实现实验复现。
..智能配置生成（SmartConfigGenerator）
..自动分析每列的信噪比（SNR）或平均强度，根据预设阈值将列划分为不同质量区间，并为每个区间分配最优的去噪策略（如低 SNR 用强滤波的小波，高 SNR 用轻量 EWMA）。
(3) 去噪执行器（SelectiveDenoiser）
..读取配置管理器，对指定列应用相应算法，并记录处理状态（成功/跳过/错误）及详细日志。
(4) 可视化工具（DenoiseVisualizer）
..二维时序对比图：并排显示原始与去噪后的信号曲线，便于局部观察。
..三维曲面图：将整个 m/z‑时间数据矩阵绘制为三维曲面，直观对比全局去噪效果（原始 vs 去噪）。
(5) 高级应用（预留扩展，为高分辨质谱作为基础扩展）
..同位素对处理（IsotopePairProcessor）：自动匹配基峰与同位素峰，对两者进行一致去噪并计算同位素丰度比。
..生物标志物处理（BiomarkerProcessor）：根据预先定义的 m/z 列表，针对性配置和去噪。
（6）工作流管理器（SelectiveDenoisingWorkflow）
..整合配置生成、执行、可视化、结果保存和报告生成，提供一键式端到端处理。
..批量处理器（BatchDenoiseProcessor）
..支持对多个数据文件进行批处理，统一输出结果和配置。

二、优势与特色
1. 高度模块化与可扩展
.. 基于抽象基类 DenoiseMethod，添加新算法只需继承并实现 denoise 方法，无需修改其他模块。
2. 灵活的配置体系
..既可以手动精细调控每一列，也可以借助智能策略自动生成，兼顾灵活性与自动化。
3. 实时统计与可视化反馈
..内置处理统计（处理列数、错误等）和多种绘图功能，便于调试和结果展示。
4. 三维整体视图
..三维曲面图能同时展现 m/z 和时间两个维度，对质谱数据的全局去噪效果一目了然。
5. 科学工作流集成
..SelectiveDenoisingWorkflow 封装了从加载数据到生成报告的完整流程，使用方便。
6. 配置持久化
..去噪配置可保存为 JSON，确保实验可重复，也便于批量处理时复用。
7. 专用处理模块
..针对同位素对和生物标志物提供专门接口。

三、设计思路概述：
1.定义一个主类，包含所有去噪方法。
2.定义一个配置管理类，用于管理各列的去噪方法和参数。
3.定义一个去噪处理器类，负责根据配置应用去噪。
4.定义一个可视化类，用于展示去噪效果。
5.定义一些辅助函数和工具类。

四、目录结构
deNoise_ms_results/               # ← 本脚本输出根目录
├── data/                          # 去噪后的数据（CSV/Excel）
│   └── batch_results/             # 批量处理的结果
├── figures/                       # 图片（PNG）
├── logs/                          # 日志文件（自动保留最近 N 个）
└── Database_my/                   # 数据库类型文件，与去噪数据本身分开管理
    ├── denoising_config*.json     # 去噪配置（持久化状态）
    └── denoising_report*.txt      # 处理报告
-- 输入路径自动定位：DEFAULT_INPUT_FILE = SCRIPT_DIR / 'cleandata_ms_results/data/cleandata_ms.csv'（来自 input_ms.py）
-- 交互式输入的安全处理
    所有 input() 默认值改为基于新目录（如 DATA_DIR_LOCAL / DB_DIR_LOCAL），直接回车即可使用默认值
    用户仍可手动指定任意目录（不会被强制覆盖）

五、完整流水线
source_ms.csv ─input_ms.py──▶ cleandata_ms_results/data/cleandata_ms.csv
                                           │
                                           ├─▶ visualization_ms.py ─▶ Visualization_ms_results/
                                           │
                                           └─▶ deNoise_ms.py ─▶ deNoise_ms_results/
                                                    ├── data/
                                                    ├── figures/
                                                    ├── logs/
                                                    └── Database_my/

load_ms_data → df (时间 × m/z)

       ▼
DenoiseConfigManager
  ├── 普通方法：set_column_config('mz44', 'wavelet', {...})
  └── 自研方法：set_mz5_11_config(['mz28','mz44'], no_gas_mz=[...])

       ▼
SelectiveDenoiser.denoise(df)
  ├── _prepare_mz5_11_params(df, columns)     ★ 新增：矩阵级预处理
  │     ├── x_ref = mean(mz5..mz11)              公式(1)
  │     ├── 对每个目标列在 T 内做互相关 → lag     公式(2)
  │     ├── k 点中心时均平滑 → X_T, Y_T          公式(3)(4)(7)(8)
  │     ├── 均方 → S_X, S_Y, S_y                 公式(5)(6)(9)(10)
  │     ├── a = sqrt(S_Y / S_X)                  公式(11)
  │     └── 数值搜索 c，使 S_z = S_y             公式(12)-(14)
  │
  ├── 逐列：普通方法 → DenoiseMethod.denoise
  └── 逐列：mz5_11  → MzBaselineNoiseDenoise.denoise
        ├── 注入 x_ref / lag / a / c / k
        ├── ỹ(t) = MA(y, k)                       公式(17)
        ├── z(t) = a·x(t+lag)+c，z̃(t) = MA(z, k)  公式(15)(16)
        └── y_g(t) = ỹ(t) − z̃(t)                  公式(18)

       ▼
denoised_df + stats + 配置 JSON + 可视化
"""
# %% ==================== 数据库文件 ================== ###
import logging
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.ndimage import uniform_filter1d, median_filter, percentile_filter
from scipy.signal import savgol_filter, find_peaks, lfilter
import pywt
import threading
import json
import os
import sys
import time
import copy
from pathlib import Path
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any, Union

# ---------- mz5_11 自研方法所需依赖 ----------
# 复用现有 uniform_filter1d，无需额外依赖

# %% ==================== 输出目录与日志配置 ==================== ###
SCRIPT_DIR = Path(__file__).resolve().parent if '__file__' in globals() else Path.cwd()

# ---------- 输入文件（来自 input_ms.py 的输出） ----------
INPUT_DIR = SCRIPT_DIR / 'cleandata_ms_results' / 'data'
DEFAULT_INPUT_FILE = INPUT_DIR / 'cleandata_ms.csv'

# ---------- 输出根目录 ----------
BASE_OUTPUT_DIR = SCRIPT_DIR / 'deNoise_ms_results'
DATA_DIR    = BASE_OUTPUT_DIR / 'data'         # 去噪后的数据
FIGURES_DIR = BASE_OUTPUT_DIR / 'figures'      # 图片（PNG）
LOGS_DIR    = BASE_OUTPUT_DIR / 'logs'         # 日志文件
DB_DIR      = BASE_OUTPUT_DIR / 'Database_my'  # 数据库类型文件（配置/报告）


def setup_output_dirs(base_dir=BASE_OUTPUT_DIR,
                      subdirs=('data', 'figures', 'logs', 'Database_my')):
    """
    创建统一的输出目录结构：
        deNoise_ms_results/
            ├── data/         去噪后的数据（CSV / Excel）
            ├── figures/      图片（PNG）
            ├── logs/         日志文件
            └── Database_my/  数据库类型文件（配置 JSON、报告 TXT）
    返回：dict，键为子目录名，值为 Path 对象。
    """
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    dirs = {}
    for sub in subdirs:
        p = base_dir / sub
        p.mkdir(parents=True, exist_ok=True)
        dirs[sub] = p
    return dirs


def _cleanup_old_logs(log_dir, log_name, keep_last_n):
    """
    清理 log_dir 下匹配 '<log_name>_*.log' 的旧日志文件，
    仅保留按修改时间排序最新的 keep_last_n 个。
    """
    if keep_last_n is None or keep_last_n <= 0:
        return []

    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []

    candidates = [p for p in log_dir.glob(f'{log_name}_*.log') if p.is_file()]

    def _safe_mtime(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    candidates.sort(key=lambda p: (_safe_mtime(p), p.name), reverse=True)
    to_delete = candidates[keep_last_n:]

    deleted = []
    for p in to_delete:
        try:
            p.unlink()
            deleted.append(p)
        except OSError as e:
            logging.warning(f"无法删除旧日志 '{p.name}': {e}")
    return deleted


def setup_logging(log_dir=LOGS_DIR, log_name='denoise_ms', keep_last_n=10):
    """
    配置 logging：
        - 控制台输出（StreamHandler）
        - 文件输出（FileHandler，写入 logs/<log_name>_YYYYmmdd_HHMMSS.log）
        - 自动清理：保留最近 keep_last_n 个日志
    返回：本次日志文件的 Path 对象。
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
    log_file = log_dir / f'{log_name}_{timestamp}.log'

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    try:
        deleted = _cleanup_old_logs(log_dir, log_name, keep_last_n)
        if deleted:
            logging.info(f"已清理 {len(deleted)} 个旧日志文件"
                         f"（保留最近 {keep_last_n} 个，含本次）")
            for p in deleted:
                logging.info(f"   🗑️ 删除: {p.name}")
    except Exception as e:
        logging.warning(f"日志清理异常（已忽略）: {e}")

    return log_file


# 保留 basicConfig 作为兜底（若脚本被导入而未调用 main()）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# %% ==================== 时间列名规范化辅助函数 ==================== ###
def _normalize_time_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    将 '##Time/min' 列名规范化为 'Time/min'（若存在且新名尚未占用）。

    说明：
      - 该函数不修改原 DataFrame，返回重命名后的副本；
      - 若原表已含 'Time/min'（例如经 input_ms.py 处理），或仅含 '##Time/min'，
        均会保证输出 DataFrame 中时间列名为 'Time/min'；
      - 供加载（load_ms_data）与保存（workflow.save_results /
        BatchDenoiseProcessor.process_files）两处统一调用，避免输出 CSV 中
        出现 '##Time/min' 列名。
    """
    if '##Time/min' in df.columns and 'Time/min' not in df.columns:
        df = df.rename(columns={'##Time/min': 'Time/min'})
        logging.info("时间列名规范化：'##Time/min' → 'Time/min'")
    return df


# %% ==================== 数据类定义 ====================
@dataclass    # 数据类，自动生成 __init__、__repr__、__eq__ 等常用方法，减少样板代码。
class DenoisingStats:
    """去噪统计信息类"""
    total_columns: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    column_details: Dict[str, Dict] = field(default_factory=dict)

    def summary(self) -> str:
        """返回统计摘要"""
        return (f"处理统计: 总列数={self.total_columns}, "
                f"已处理={self.processed}, 已跳过={self.skipped}, 错误={self.errors}")

@dataclass
class ColumnConfig:
    """列配置类"""
    method: str
    params: Dict[str, Any] = field(default_factory=dict)    # field函数的使用，确保了每个实例拥有独立的字典，互不影响。

# %% ==================== 抽象基类 ====================
class DenoiseMethod(ABC):
    """去噪方法抽象基类"""

    @abstractmethod    # 抽象方法，包含至少一个该语句的类不能直接实例化，一般用于定义接口或基类，强制子类提供特定行为。
    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        """去噪方法接口"""
        pass

    @staticmethod    # 静态方法，是属于类而不是实例的方法，不接受self（实例本身）或cls（类本身）作为第一个参数。可以像普通函数一样工作
    def create(method_name: str) -> 'DenoiseMethod':
        """
        创建具体去噪方法实例，工厂模式
        """
        method_classes = {
            'mean': MeanDenoise,
            'std_threshold': StdThresholdDenoise,
            'ewma': EWMADenoise,
            'median': MedianDenoise,
            'quantile': QuantileDenoise,
            'savitzky_golay': SavitzkyGolayDenoise,
            'wavelet': WaveletDenoise,
            'kalman': KalmanDenoise,
            'mz5_11': MzBaselineNoiseDenoise,    # ★ 新增：自研方法(9)：利用mz5_11信号/无气体信号滑动去噪
            'none': NoneDenoise
        }

        if method_name not in method_classes:
            raise ValueError(f"未知的去噪方法: {method_name}")

        return method_classes[method_name]()


# %% ==================== 具体去噪方法类 ====================
"""
提供以下去噪方法：
1. 滑动平均（mean）
2. 标准差阈值（std_threshold）
3. 指数加权移动平均（ewma）
4. 滑动中值（median）
# 4. 双重指数加权移动平均（double_ewma）
5. 分位数滤波（quantile）
6. Savitzky-Golay滤波（savitzky_golay）
7. 小波去噪（wavelet）
8. 卡尔曼滤波（kalman）
9. 利用mz5_11信号/无气体信号滑动去噪（自研）
10.不处理（none）
11.其他方法

### Need：把各方法的参数作为可由用户调整或输入的界面/按钮等

"""
class MeanDenoise(DenoiseMethod):
    """
    1.简单滑动平均去噪：
    DenoiseMethod 中定义了 denoise 抽象方法，所以子类必须实现该方法。
    time_series：一维 NumPy 数组，表示某个 m/z 值随时间变化的信号强度序列
    从 params 字典中获取 window_size 键对应的值，如果未提供则默认为 5；滑动窗口的宽度（点数）
    返回与输入 time_series 形状相同的 NumPy 数组，其中每个点已被替换为对应滑动窗口内的平均值。噪声被抑制，信号趋势得以保留。
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        window_size = params.get('window_size', 5)

        return uniform_filter1d(time_series, size=window_size, mode='reflect')

class StdThresholdDenoise(DenoiseMethod):
    """
    2. 标准差阈值去噪（向量化版本）
    通过滑动窗口的局部均值和标准差动态计算阈值，低于阈值的信号置零。
    使用累积和向量化计算，避免 Python 循环，大幅提升长序列处理速度。
    边界处理与原实现一致：窗口在序列两端自动截断。
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        window_size = params.get('window_size', 11)
        sigma_multiple = params.get('sigma_multiple', 3)

        n = len(time_series)
        if n == 0:
            return time_series.copy()

        half_window = window_size // 2

        # 计算累积和与累积平方和（前面补 0 便于差分）
        cumsum = np.cumsum(np.insert(time_series, 0, 0))
        cumsum_sq = np.cumsum(np.insert(time_series ** 2, 0, 0))

        # 每个点的窗口左右边界（向量化）
        t = np.arange(n)
        L = np.maximum(0, t - half_window)
        R = np.minimum(n, t + half_window + 1)
        lengths = R - L

        # 窗口和、窗口平方和
        window_sum = cumsum[R] - cumsum[L]
        window_sq_sum = cumsum_sq[R] - cumsum_sq[L]

        # 滑动均值与标准差
        sliding_mean = window_sum / lengths
        # 方差 = E[x^2] - (E[x])^2，避免浮点误差导致负数
        variance = (window_sq_sum / lengths) - sliding_mean ** 2
        variance = np.maximum(variance, 0.0)
        sliding_std = np.sqrt(variance)

        threshold = sliding_mean + sigma_multiple * sliding_std

        denoised = time_series.copy()
        denoised[time_series < threshold] = 0.0

        return denoised

class EWMADenoise(DenoiseMethod):
    """
    3. 指数加权移动平均（EWMA）—— lfilter 向量化版本
    利用一阶 IIR 滤波器实现，结果与原递归实现完全一致。
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        alpha = params.get('alpha', 0.3)
        if len(time_series) == 0:
            return time_series.copy()

        # 差分方程：y[n] = alpha * x[n] + (1 - alpha) * y[n-1]
        # 对应滤波器系数 b = [alpha], a = [1, -(1-alpha)]
        b = [alpha]
        a = [1.0, -(1.0 - alpha)]
        # 初始条件：使 y[0] = x[0]
        zi = [(1.0 - alpha) * time_series[0]]

        y, _ = lfilter(b, a, time_series, zi=zi)
        return y

class MedianDenoise(DenoiseMethod):
    """
    4.滑动中值去噪
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        window_size = params.get('window_size', 5)

        return median_filter(time_series, size=window_size, mode='reflect')

class QuantileDenoise(DenoiseMethod):
    """
    5.分位数滤波
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        window_size = params.get('window_size', 11)
        quantile = params.get('quantile', 0.75)

        quantile_values = percentile_filter(
            time_series,
            percentile=quantile * 100,
            size=window_size,
            mode='reflect'
        )
        mask = time_series >= quantile_values

        return time_series * mask

class SavitzkyGolayDenoise(DenoiseMethod):
    """
    6. Savitzky-Golay滤波
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        window_size = params.get('window_size', 11)
        polyorder = params.get('polyorder', 3)

        if len(time_series) >= window_size:
            return savgol_filter(time_series, window_size, polyorder, mode='interp')
        else:
            return uniform_filter1d(time_series, size=min(3, len(time_series)), mode='reflect')

class WaveletDenoise(DenoiseMethod):
    """
    7.小波去噪：适合离线处理复杂信号
    """
    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        if len(time_series) < 8:  # 数据过短，降级
            return uniform_filter1d(time_series, size=min(3, len(time_series)), mode='reflect')

        wavelet = params.get('wavelet', 'db4')
        user_level = params.get('level', 3)
        threshold_type = params.get('threshold_type', 'soft')

        # 关键修复：根据信号长度和小波类型自动限制最大分解层数,20260912
        max_level = pywt.dwt_max_level(len(time_series), pywt.Wavelet(wavelet).dec_len)
        level = max(1, min(user_level, max_level)) if max_level >= 1 else 1
        if level != user_level:
            logging.info("[WaveletDenoise] level 从 %d 自动调整为 %d（数据长度 %d）",
                        user_level, level, len(time_series))

        coeffs = pywt.wavedec(time_series, wavelet, level=level)    # 1.小波分解，得到一系列的小波系数

        # 使用最高频细节系数估计噪声 sigma
        sigma = np.median(np.abs(coeffs[-1])) / 0.6745 if len(coeffs) > 1 else 0.0
        uthresh = sigma * np.sqrt(2 * np.log(len(time_series))) if len(time_series) > 0 else 0.0

        coeffs_thresh = [coeffs[0]]
        for i in range(1, len(coeffs)):
            coeffs_thresh.append(pywt.threshold(coeffs[i], uthresh, threshold_type))

        denoised = pywt.waverec(coeffs_thresh, wavelet)    # 3. 小波重构，小波逆变换，得到去噪后的结果

        # 确保长度一致
        if len(denoised) > len(time_series):
            denoised = denoised[:len(time_series)]
        elif len(denoised) < len(time_series):
            denoised = np.pad(denoised, (0, len(time_series) - len(denoised)))

        return denoised

class KalmanDenoise(DenoiseMethod):
    """
    8.卡尔曼滤波: 实时动态系统的首选
    """

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        process_variance = params.get('process_variance', 1e-5)
        measurement_variance = params.get('measurement_variance', 0.1)

        n = len(time_series)
        x = time_series[0]
        P = 1.0
        Q = process_variance
        R = measurement_variance

        filtered = np.zeros(n)

        for t in range(n):
            x_pred = x
            P_pred = P + Q

            K = P_pred / (P_pred + R)
            x = x_pred + K * (time_series[t] - x_pred)
            P = (1 - K) * P_pred

            filtered[t] = x

        return filtered

# ======================================================================
# 模块级辅助函数，主要质谱底谱噪声自适应智能处理方法（也即方法9.mz5_11信号/无气体信号滑动去噪）
# ======================================================================
def _shift_signal(signal: np.ndarray, lag: int) -> np.ndarray:
    """
    按 lag 平移信号：result[i] = signal[i + lag]（越界用边界值填充）。
    对应公式(15)中的 x(t + Δτ)。
    """
    n = len(signal)
    result = np.zeros_like(signal, dtype=float)
    if lag == 0:
        return signal.copy()
    if lag > 0:
        result[:n - lag] = signal[lag:]
        result[n - lag:] = signal[-1]
    else:
        result[-lag:] = signal[:n + lag]
        result[:-lag] = signal[0]
    return result


def _find_best_lag(x: np.ndarray, y: np.ndarray, max_lag: int = 30) -> int:
    """
    在标定时段内寻找最佳滞后 lag，使 y(t) ≈ x(t + lag)。
    对应公式(2)：rho_xy(Δτ) -> 1。
    """
    n = len(x)
    if n < 2 * max_lag:
        max_lag = max(1, n // 4)

    best_lag, best_corr = 0, -np.inf
    start = max_lag
    end = n - max_lag
    if end <= start:
        start, end = 0, n

    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x_seg = x[start:end]
            y_seg = y[start + lag:end + lag] if end + lag <= n else y[start + lag:]
        else:
            x_seg = x[start - lag:end - lag] if start - lag >= 0 else x[:end - lag]
            y_seg = y[start:end]

        m = min(len(x_seg), len(y_seg))
        if m < 10:
            continue
        x_seg, y_seg = x_seg[:m], y_seg[:m]

        if np.std(x_seg) == 0 or np.std(y_seg) == 0:
            continue
        corr = np.corrcoef(x_seg, y_seg)[0, 1]
        if corr > best_corr:
            best_corr, best_lag = corr, lag
    return best_lag


def _solve_c_numeric(a: float, x_T: np.ndarray, y_T: np.ndarray, S_y: float,
             n_search: int = 2000) -> float:
    """
    数值求解 c，使 S_z = S_y（公式(12)-(14)）。
    z(t) = a*x(t) + c, S_z = mean(z^2)
    作为备用求解器，暂未启用。
    优先使用_solve_c解析求解，再考虑数值搜索。
    数值搜索范围根据数据量级自适应调整。
    n_search: 网格搜索点数，控制计算精度与速度的权衡。
    """
    data_scale = max(np.std(y_T), np.std(x_T), 1e-12)
    c_candidates = np.linspace(-5 * data_scale, 5 * data_scale, n_search)
    best_c, best_err = 0.0, np.inf
    for c in c_candidates:
        S_z = np.mean((a * x_T + c) ** 2)
        err = abs(S_z - S_y)
        if err < best_err:
            best_err, best_c = err, c
    return best_c


def _solve_c(a: float, x_T: np.ndarray, y_T: np.ndarray, S_y: float) -> float:
    """
    解析求解 c，使 S_z = S_y（公式(12)-(14)）。
        z(t) = a*x(t) + c
        S_z = mean(z^2) = S_y
    """
    m1 = float(np.mean(x_T))
    m2 = float(np.mean(x_T ** 2))
    var_x = max(m2 - m1 ** 2, 0.0)
    delta = S_y - a * a * var_x
    if delta < 0:
        # 无实解：取让 z 的均值为 0 的 c，作为最接近的可行解
        return -a * m1
    sqrt_delta = np.sqrt(delta)
    c1 = -a * m1 + sqrt_delta
    c2 = -a * m1 - sqrt_delta
    # 取绝对值较小者，更稳定（避免引入大的直流偏置）
    return c1 if abs(c1) <= abs(c2) else c2

# ======================================================================
# 自研去噪方法：利用 m/z 5-11 无气体通道底谱自适应处理（方法9）
# 对应公式(1)-(18)
# ======================================================================
class MzBaselineNoiseDenoise(DenoiseMethod):
    """
    9. 自研方法：利用 m/z 5-11 等无气体通道的底谱噪声作为参考，
    自适应地去除含气体 m/z 通道的低频漂移与底谱噪声。

    ★ 与其他方法的区别：
      本方法"按列调用"，但依赖矩阵级预处理结果。
      SelectiveDenoiser 会在调用本方法前，把以下参数注入到 params：
        - x_ref  : 参考底谱信号（全时域），公式(1)
        - lag    : 该列与 x_ref 的最佳滞后，公式(2)
        - a      : 幅度变换系数，公式(11)
        - c      : 均值位移差，公式(12)-(14)
        - k      : 中心时均平滑窗口点数

    ★ 若这些参数缺失（例如单独调用本方法、或未做矩阵预处理），
       则退化为对信号本身做 k 点中心时均平滑，保证鲁棒性。
    """
    _degrade_warned = False

    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        cls = MzBaselineNoiseDenoise

        y = np.asarray(time_series, dtype=float)

        # 退化路径：缺少预计算参数
        need = ('x_ref', 'lag', 'a', 'c', 'k')
        if not all(key in params for key in need):
            k = int(params.get('k', 5))
            k = max(3, k)
            k = k if k % 2 == 1 else k + 1
            if not cls._degrade_warned:
                logging.warning(
                    "[MzBaselineNoiseDenoise] 缺少预计算参数，退化为 k=%d 点平滑。"
                    "后续同类警告将不再打印。", k
                )
                cls._degrade_warned = True
            return uniform_filter1d(y, size=k, mode='reflect')

        x_ref = np.asarray(params['x_ref'], dtype=float)
        lag = int(params['lag'])
        a = float(params['a'])
        c = float(params['c'])
        k = int(params['k'])
        k = max(3, k)              # ★ 保证 ≥ 3
        k = k if k % 2 == 1 else k + 1

        if x_ref.shape[0] != y.shape[0]:
            # 长度不一致，退化为平滑
            logging.warning(
                "[MzBaselineNoiseDenoise] x_ref 长度 (%d) 与 y (%d) 不一致，退化为平滑。",
                x_ref.shape[0], y.shape[0]
            )
            return uniform_filter1d(y, size=k, mode='reflect')

        # 公式(17)：对实际信号 y(t) 做 k 点中心时均平滑
        y_tilde = uniform_filter1d(y, size=k, mode='reflect')

        # 公式(15)：构造仿真信号 z(t) = a * x(t + Δτ) + c
        x_shifted = _shift_signal(x_ref, lag)
        z = a * x_shifted + c

        # 公式(16)：对 z(t) 做同样平滑，得到底谱低频漂移
        z_tilde = uniform_filter1d(z, size=k, mode='reflect')

        # 公式(18)：扣除低频漂移
        y_g = y_tilde - z_tilde
        return y_g

    @classmethod
    def reset_warning(cls):
        cls._degrade_warned = False


class NoneDenoise(DenoiseMethod):
    """
    10.不处理
    """
    def denoise(self, time_series: np.ndarray, **params) -> np.ndarray:
        return time_series


# ======================================================================
# 11. 其他方法
# ======================================================================

# %% ==================== 配置管理类 ====================
class DenoiseConfigManager:
    """
    去噪配置管理器：负责管理每列的去噪配置（方法、参数、按 SNR/强度批量配置等）
    """

    # 默认参数配置
    '''
    # 这是一个类属性（Class Attribute），存储了各种去噪方法的默认参数集，供所有实例共享。
    # 作用：集中管理所有支持的算法及其默认超参数，便于后续根据方法名快速获取对应参数。
    '''

    # 新增类属性：缓存列名到 m/z 数值的映射
    _mz_cache: Dict[str, Optional[float]] = {}
    _mz_cache_lock = threading.Lock()

    DEFAULT_PARAMS = {
        'mean': {'window_size': 5},
        'std_threshold': {'window_size': 11, 'sigma_multiple': 3},
        'ewma': {'alpha': 0.3},
        'median': {'window_size': 5},
        'quantile': {'window_size': 11, 'quantile': 0.75},
        'savitzky_golay': {'window_size': 11, 'polyorder': 3},
        'wavelet': {'wavelet': 'db4', 'level': 3, 'threshold_type': 'soft'},
        'kalman': {'process_variance': 1e-5, 'measurement_variance': 0.1},

        # ★ 自研方法默认参数（可在 set_column_config 时覆盖）
        'mz5_11': {
            'no_gas_mz': ['mz5', 'mz6', 'mz7', 'mz8', 'mz9', 'mz10', 'mz11'],
            'T_idx': None,          # None → 自动取前 10% 时间点作为标（特）定时段
            'T_frac': 0.1,          # 自动取标定时段的比例（当 T_idx=None）
            'k': 5,                 # 平滑窗口（奇数）
            'max_lag': 30,          # 互相关搜索最大滞后
            'aggregation': 'mean',  # 无气体通道组合方式：'mean' 目前唯一实现
        },
        'none': {},
    }

    def __init__(self, default_method: str = 'savitzky_golay'):
        """
        初始化配置管理器

        Args:
            default_method: 默认去噪方法
        """
        self.default_method = default_method    # 实例化时可以指定一个默认的去噪方法名称，默认为 'savitzky_golay'
        self.default_params = self.DEFAULT_PARAMS.get(default_method, {}).copy()
        self.column_configs: Dict[str, ColumnConfig] = {}    # 实例属性，值是 ColumnConfig 对象
                                              #（另一个类，通常用于存储某列的自定义配置，如是否启用去噪、使用哪种方法、参数等）

    _TIME_COLUMN_PATTERN = re.compile(
        r'(^|[^a-z])'
        r'(time|rt|retention|scan|index|sample|id|name|tag|label|batch|group)'
        r'([^a-z]|$)',
        re.IGNORECASE
    )

    @staticmethod
    def _is_time_column(col: str) -> bool:
        """
        判断列名是否像时间/索引/元数据列（而非 m/z 列）。
        识别示例：'##Time/min', 'Time', 'RT', 'Retention Time',
                'Scan', 'ScanNumber', 'Index', 'SampleID', 'Name' 等。
        """
        if not isinstance(col, str):
            return False

        # 1) 纯数字 → 肯定是 m/z
        try:
            float(col)
            return False
        except ValueError:
            pass

        # 2) 以 mz 开头 → 肯定是 m/z
        if re.match(r'^\s*mz[_\- ]?\s*\d', col, re.IGNORECASE):
            return False

        # 3) 关键词匹配
        return bool(DenoiseConfigManager._TIME_COLUMN_PATTERN.search(col))

    @staticmethod
    def _extract_mz_from_column(col: str) -> Optional[float]:
        """
        从列名中提取 m/z 数值（带缓存，严格匹配）。
        支持格式：
        - 纯数字：'28.0'、'44.5'
        - mz前缀：'mz28'、'mz_28.0'、'mz-28'、'mz 28.0'
        - MZ前缀（大小写不敏感）
        无法识别（如 '##Time/min'、'Time1'、'Sample_A'）返回 None。
        """
        with DenoiseConfigManager._mz_cache_lock:
            if col in DenoiseConfigManager._mz_cache:
                return DenoiseConfigManager._mz_cache[col]

        value: Optional[float] = None

        # 方法1：纯数字
        try:
            value = float(col)
        except (ValueError, TypeError):
            value = None

        # 方法2：严格 mz 前缀
        if value is None and isinstance(col, str):
            match = re.match(r'^\s*mz[_\- ]?\s*(\d+\.?\d*|\d*\.\d+)\s*$', col, re.IGNORECASE)
            if match:
                try:
                    value = float(match.group(1))
                except ValueError:
                    value = None

        DenoiseConfigManager._mz_cache[col] = value
        return value

    VALID_METHODS = {
        'mean', 'std_threshold', 'ewma', 'median', 'quantile',
        'savitzky_golay', 'wavelet', 'kalman',
        'mz5_11',    # ★ 新增：自研方法(9)：利用mz5_11信号/无气体信号滑动去噪
        'none'
    }

    def set_column_config(self, column: str, method: Optional[str] = None,
                         params: Optional[Dict[str, Any]] = None) -> 'DenoiseConfigManager':
        """
        设置特定列的配置

        Args:
            column: 列名
            method: 去噪方法，None表示使用默认方法
            params: 方法参数，None表示使用默认参数

        Returns:
            返回实例自身，self，支持链式调用，例如 obj.set_column_config(...).set_column_config(...)
        """
        if method is None:
            method = self.default_method

        # 关键校验
        if method not in self.VALID_METHODS:
            raise ValueError(
                f"未知去噪方法 '{method}'。可用方法: {sorted(self.VALID_METHODS)}"
            )

        if params is None:
            params = self.DEFAULT_PARAMS.get(method, {}).copy()

        self.column_configs[column] = ColumnConfig(method=method, params=params)

        return self

    def set_multiple_columns(self, config_dict: Dict[str, Dict]) -> 'DenoiseConfigManager':
        """
        批量设置多列配置

        Args:
            config_dict: 配置字典，格式为{'column': {'method': ..., 'params': ...}}

        Returns:
            返回self以支持链式调用
        """
        for column, config in config_dict.items():
            self.set_column_config(
                column,
                config.get('method'),
                config.get('params')
            )

        return self

    def set_columns_by_mz_range(self, df: pd.DataFrame, mz_range: Tuple[float, float],
                              method: str, params: Optional[Dict] = None,
                              mz_columns: Optional[List[str]] = None) -> List[str]:
        """
        为m/z范围内的所有列设置相同的配置

        Args:
            df: 数据框，列名应为可转换为浮点数的 m/z 值（如 '100.2'、'250.5'）
            mz_range: m/z范围，一个二元组 (lower, upper)，指定 m/z 的区间范围（包含端点）
            method: 去噪方法
            params: 方法参数

        Returns:
            实际被设置了配置的列名列表
        """
        if params is None:
            params = self.DEFAULT_PARAMS.get(method, {}).copy()

        if mz_columns is not None:
            candidates = mz_columns
        else:
            # 关键修复：显式过滤掉时间/索引/元数据列
            candidates = [
                col for col in df.columns
                if not self._is_time_column(col)
            ]

        columns_in_range = []
        for col in candidates:
            try:
                mz_val = self._extract_mz_from_column(col)
                if mz_val is None:
                    continue
                if mz_range[0] <= mz_val <= mz_range[1]:
                    self.set_column_config(col, method, params)
                    columns_in_range.append(col)
            except (ValueError, TypeError):
                continue

        return columns_in_range

    def set_columns_by_intensity(self, df: pd.DataFrame, threshold: float = 1.0e-10,
                               method: str = 'wavelet', params: Optional[Dict] = None) -> List[str]:
        """
        为强度高于阈值的列设置配置

        Args:
            df: 数据框
            threshold: 强度阈值
            method: 去噪方法
            params: 方法参数

        Returns:
            设置的列列表
        """
        if params is None:
            params = self.DEFAULT_PARAMS.get(method, {}).copy()

        avg_intensities = df.mean()    # 计算每列的均值（即mz特征在所有样本中的平均信号强度），返回值为pandas series
        high_intensity_cols = avg_intensities[avg_intensities > threshold].index.tolist()

        for col in high_intensity_cols:
            self.set_column_config(col, method, params)

        return high_intensity_cols

    def get_column_config(self, column: str) -> ColumnConfig:
        """
        获取列的配置
        Args:
            column: 列名
        Returns:
            列配置对象
        很好地处理了“列配置缺失”的情况：返回一个使用默认方法和默认参数的新 ColumnConfig 对象。
        未显式配置的列会自动采用你设定的默认去噪策略（例如 'savitzky_golay'），而不是被跳过。
        """
        if column in self.column_configs:
            return self.column_configs[column]
        else:
            return ColumnConfig(method=self.default_method, params=self.default_params.copy())

    # ---------- 方法9：mz5_11 自研去噪，使用 ----------
    def set_mz5_11_config(
        self,
        columns: List[str],
        no_gas_mz: Optional[List[str]] = None,
        T_idx: Optional[Union[slice, Tuple[int, int]]] = None,
        T_frac: float = 0.1,
        k: int = 5,
        max_lag: int = 30,
    ) -> 'DenoiseConfigManager':
        """
        便捷方法：为一组含气体列批量设置 'mz5_11' 自研去噪。
        """
        if no_gas_mz is None:
            no_gas_mz = self.DEFAULT_PARAMS['mz5_11']['no_gas_mz']

        params = {
            'no_gas_mz': list(no_gas_mz),
            'T_idx': T_idx,
            'T_frac': T_frac,
            'k': k,
            'max_lag': max_lag,
            'aggregation': 'mean',
        }
        for col in columns:
            self.set_column_config(col, 'mz5_11', copy.deepcopy(params))
        return self


    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'default_method': self.default_method,
            'default_params': self.default_params,
            'column_configs': {
                col: {'method': config.method, 'params': config.params}
                for col, config in self.column_configs.items()
            }
        }

    @classmethod
    def from_dict(cls, config_dict: Dict) -> 'DenoiseConfigManager':
        """
        从字典创建配置管理器：恢复了之前保存的每列的自定义配置。
        @classmethod 是 Python 中的一个装饰器，用来定义类方法。类方法的第一个参数是 cls，代表类本身（而不是类的实例 self）
        它充当替代构造函数，用于从字典中恢复或创建 DenoiseConfigManager 的实例。
        该方法返回一个该类的实例。
        """
        '''
        调用 cls(...) 等同于 DenoiseConfigManager(...)，即调用类的 __init__ 方法。
        从字典中获取 'default_method' 键的值，如果不存在则使用默认值 'savitzky_golay'。
        这样就创建了一个配置管理器实例，并保存在 manager 变量中。
        '''
        manager = cls(config_dict.get('default_method', 'savitzky_golay'))
        manager.default_params = config_dict.get('default_params', {}).copy()

        for col, config in config_dict.get('column_configs', {}).items():
            manager.set_column_config(col, config.get('method'), config.get('params'))

        return manager

    def save_to_file(self, filepath: str) -> None:
        """
        保存配置到文件:
        用于将 DenoiseConfigManager 实例的当前配置保存到一个 JSON 文件中，以便后续可以通过 from_dict 或类似方法恢复。
        自动创建不存在的父目录
        """
        config_dict = self.to_dict()

        # 转换numpy类型为Python原生类型
        def convert_types(obj):
            if isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, slice):                        # ★ 新增,约定 T_idx 用 [start, stop] 列表
                return [obj.start, obj.stop]
            elif isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(i) for i in obj]
            else:
                return obj

        config_dict = convert_types(config_dict)

        # 自动创建父目录，使用pathlib 处理路径，跨平台更安全
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)


        with path.open('w', encoding='utf-8') as f:    # 以写入模式（'w'）打开文件（如果文件不存在则创建，存在则覆盖）
            json.dump(config_dict, f, indent=2, ensure_ascii=False)    # indent=2 使生成的 JSON 文件具有缩进，便于阅读和调试

        print(f"配置已保存到 {filepath}")

    @classmethod
    def load_from_file(cls, filepath: str) -> 'DenoiseConfigManager':
        """
        从文件加载配置:
        从之前通过 save_to_file 保存的 JSON 文件中读取配置字典，并重建一个 DenoiseConfigManager 实例。
        这是 save_to_file 的逆操作，实现了配置的持久化加载。
        依赖 json 模块；如果文件包含非 UTF-8 编码，可能需要指定 encoding='utf-8'
        """
        with open(filepath, 'r', encoding='utf-8') as f:    # 以只读模式打开指定的 JSON 文件。
            config_dict = json.load(f)    # 将文件中的 JSON 文本解析为 Python 字典（config_dict）

        return cls.from_dict(config_dict)

    def copy(self) -> 'DenoiseConfigManager':
        """
        返回当前配置管理器的深拷贝。
        确保修改副本不会影响原始对象。
        """
        # 创建新实例，使用相同的默认方法
        new_manager = DenoiseConfigManager(default_method=self.default_method)
        # 复制默认参数字典
        new_manager.default_params = self.default_params.copy()
        # 遍历每个列的配置，复制 ColumnConfig 对象
        for col, config in self.column_configs.items():
            new_manager.column_configs[col] = ColumnConfig(
                method=config.method,
                params=config.params.copy()   # 字典深拷贝（一层复制已足够）
            )
        return new_manager

    def print_summary(self) -> None:
        """打印配置摘要"""
        print("==========配置摘要:=========")
        print(f"  默认方法: {self.default_method}")
        print(f"  已配置列数: {len(self.column_configs)}")

        # 统计各方法使用次数
        method_counts = {}
        for config in self.column_configs.values():
            method = config.method
            method_counts[method] = method_counts.get(method, 0) + 1

        print("\n  方法使用统计:")
        for method, count in method_counts.items():
            print(f"    {method}: {count} 列")

    @classmethod
    def clear_mz_cache(cls):
        """清空 m/z 解析缓存"""
        with cls._mz_cache_lock:
            cls._mz_cache.clear()

# %% ==================== 去噪处理器类（执行层） ====================
class SelectiveDenoiser:
    """
    选择性去噪处理器：
    是对配置管理器的执行层，它读取配置管理器中的配置，对传入的数据（可能是 DataFrame 的某列或整体）应用相应的去噪算法，并记录统计信息。
    """

    def __init__(self, config_manager: Optional[DenoiseConfigManager] = None):
        """
        初始化选择性去噪处理器

        Args:
            config_manager: 配置管理器，为None时创建默认配置
            引入缓存机制可以避免重复创建，实现单例模式的变体：相同方法名只实例化一次，后续直接复用。
            添加一个实例属性 self._method_cache（字典），在 denoise 方法中使用它来缓存已经创建的去噪器对象。
        """
        self.config_manager = config_manager or DenoiseConfigManager()
        self.stats_history: List[DenoisingStats] = []
        self._method_cache = {}   # 新增：缓存去噪器实例 {method_name: instance}
        self._all_no_gas_columns: set = set()  # 新增20260928：缓存所有无气体参考列名

    def denoise(self, df: pd.DataFrame, columns: Optional[List[str]] = None,
                apply_to_all: bool = False) -> Tuple[pd.DataFrame, DenoisingStats]:
        """
        应用选择性去噪。

        ★ 新增：对自研方法 'mz5_11' 做矩阵级预处理（构造参考底谱 x_ref，
          逐列计算 lag/a/c），再按列应用。
        """
        df_denoised = df.copy()
        stats = DenoisingStats()

        # ---------- 1. 确定要处理的列 ----------
        if columns is None:
            if apply_to_all:
                columns_to_process = df.columns.tolist()
            else:
                columns_to_process = list(self.config_manager.column_configs.keys())
        else:
            columns_to_process = columns
        stats.total_columns = len(columns_to_process)

        # ---------- 2. 矩阵级预处理：收集所有 mz5_11 列的调用参数 ----------
        mz5_11_call_params = self._prepare_mz5_11_params(df, columns_to_process)

        # ---------- 3. 逐列去噪 ----------
        for col in columns_to_process:
            if col not in df.columns:
                stats.column_details[col] = {'status': 'skipped', 'reason': '列不存在'}
                stats.skipped += 1
                continue

            col_config = self.config_manager.get_column_config(col)

            # 若该列被显式列为无气体参考列（出现在 no_gas_mz 中），跳过
            if col in self._all_no_gas_columns:
                df_denoised[col] = df[col].values
                stats.column_details[col] = {
                    'status': 'skipped',
                    'reason': '该列为 mz5_11 方法的参考底谱通道，不参与处理'
                }
                stats.skipped += 1
                continue

            if col_config.method == 'none':
                df_denoised[col] = df[col].values
                stats.column_details[col] = {'status': 'skipped', 'reason': '配置为不处理'}
                stats.skipped += 1
                continue

            time_series = df[col].values
            method_name = col_config.method

            # ★ 对 mz5_11 方法：注入预计算参数（x_ref / lag / a / c / k）
            if method_name == 'mz5_11':
                call_params = dict(col_config.params or {})
                injected = mz5_11_call_params.get(col, None)
                if injected is None:
                    # 该列未能在预处理中生成参数（如无参考列），退化为不处理
                    df_denoised[col] = time_series
                    stats.column_details[col] = {
                        'status': 'skipped',
                        'reason': 'mz5_11 预处理失败（缺少参考通道或数据）'
                    }
                    stats.skipped += 1
                    continue
                call_params.update(injected)
            else:
                call_params = col_config.params or {}

            try:
                if method_name not in self._method_cache:
                    self._method_cache[method_name] = DenoiseMethod.create(method_name)
                method_instance = self._method_cache[method_name]

                denoised_series = method_instance.denoise(time_series, **call_params)
                df_denoised[col] = denoised_series
                stats.column_details[col] = {'status': 'processed', 'method': method_name}
                stats.processed += 1

            except Exception as e:
                df_denoised[col] = time_series
                stats.column_details[col] = {
                    'status': 'error', 'method': method_name, 'error': str(e)
                }
                stats.errors += 1

        self.stats_history.append(stats)
        return df_denoised, stats

    # ------------------------------------------------------------------
    # 新增20260928：mz5_11 方法的矩阵级预处理（构造参考底谱 x_ref，逐列计算 lag/a/c，再逐列应用去噪器）
    # ------------------------------------------------------------------
    def _prepare_mz5_11_params(self, df: pd.DataFrame,
                               columns_to_process: List[str]) -> Dict[str, Dict]:
        """
        对使用 'mz5_11' 方法的列，预先计算每列的统计特征参数：
            x_ref（全时域参考底谱）、lag、a、c、k
        返回：{column_name: {'x_ref': ..., 'lag': ..., 'a': ..., 'c': ..., 'k': ...}}
        """
        result: Dict[str, Dict] = {}

        # 1. 找出所有使用 mz5_11 的列，以及它们声明的 no_gas_mz
        target_cols: List[str] = []
        no_gas_union: set = set()
        for col in columns_to_process:
            if col not in df.columns:
                continue
            cfg = self.config_manager.get_column_config(col)
            if cfg.method != 'mz5_11':
                continue
            target_cols.append(col)
            params = cfg.params or {}
            for c in params.get('no_gas_mz', []):
                # 1) 整数索引
                if isinstance(c, (int, np.integer)) and not isinstance(c, bool):
                    if 0 <= c < df.shape[1]:
                        no_gas_union.add(df.columns[c])
                # 2) 浮点 m/z 数值 → 通过 _extract_mz_from_column 反查
                elif isinstance(c, (float, np.floating)):
                    best_col, best_err = None, 1e9
                    for cand_col in df.columns:
                        mz = DenoiseConfigManager._extract_mz_from_column(cand_col)
                        if mz is None:
                            continue
                        err = abs(mz - float(c))
                        if err < best_err:
                            best_err, best_col = err, cand_col
                    if best_col is not None and best_err < 0.5:    # 容差
                        no_gas_union.add(best_col)
                # 3) 列名
                elif isinstance(c, str) and c in df.columns:
                    no_gas_union.add(c)

        # 缓存供主循环查询，用于跳过参考列
        self._all_no_gas_columns = no_gas_union

        if not target_cols or not no_gas_union:
            return result

        # 2. 用所有无气体通道的均值构造参考底谱 x(t)，公式(1)
        no_gas_cols_sorted = [c for c in df.columns if c in no_gas_union]
        if not no_gas_cols_sorted:
            # 修复：no_gas_union 中可能只有不在 df 中的字符串，此时无法构造 x_ref
            logging.warning("[mz5_11] 未能在数据框中找到任何无气体参考列，跳过预处理。")
            return result
        x_ref = df[no_gas_cols_sorted].astype(float).mean(axis=1).to_numpy()
        n_time = len(x_ref)

        # 3. 对每个目标列，逐列在 T 时段内计算 lag/a/c
        for col in target_cols:
            cfg = self.config_manager.get_column_config(col)
            params = cfg.params or {}

            # 解析 k 与 max_lag
            k = int(params.get('k', 5))
            k = max(3, k)              # ★ 保证 ≥ 3
            k = k if k % 2 == 1 else k + 1
            max_lag = int(params.get('max_lag', 30))

            # 解析标定时段 T
            T_idx = params.get('T_idx', None)
            if T_idx is None:
                T_frac = float(params.get('T_frac', 0.1))
                T_end = max(10, int(n_time * T_frac))
                T_slice = slice(0, T_end)
            elif isinstance(T_idx, slice):
                T_slice = T_idx
            elif isinstance(T_idx, (list, tuple)) and len(T_idx) == 2:
                T_slice = slice(int(T_idx[0]), int(T_idx[1]))
            else:
                T_slice = slice(0, max(10, int(n_time * 0.1)))

            try:
                y = df[col].astype(float).to_numpy()
                x_T = x_ref[T_slice]
                y_T = y[T_slice]

                # 公式(2)：最佳滞后
                lag = _find_best_lag(x_T, y_T, max_lag=max_lag)

                # 公式(3)-(6)：参考底谱统计特征
                x_tilde_T = uniform_filter1d(x_T, size=k, mode='reflect')
                X_T = x_T - x_tilde_T
                S_X = float(np.mean(X_T ** 2))

                # 公式(7)-(10)：实际信号统计特征
                y_tilde_T = uniform_filter1d(y_T, size=k, mode='reflect')
                Y_T = y_T - y_tilde_T
                S_Y = float(np.mean(Y_T ** 2))
                S_y = float(np.mean(y_T ** 2))

                # 计算参考底谱和实际信号的相对波动率
                x_std = np.std(x_T)
                y_std = np.std(y_T)
                x_rel = x_std / (np.mean(np.abs(x_T)) + 1e-300)
                y_rel = y_std / (np.mean(np.abs(y_T)) + 1e-300)

                # 相对波动率过小，说明信号太稳，没有信息量
                if x_rel < 0.01 or y_rel < 0.01:
                    logging.warning(f"[mz5_11] col={col} 相对波动过小 (x_rel={x_rel:.4f}, y_rel={y_rel:.4f})，跳过")
                    continue

                # 公式(11)：幅度变换系数
                a = float(np.sqrt(S_Y / S_X)) if S_X > 0 else 1.0
                # 健康的 a 应在 0.5~2 之间。低于 0.5 或高于 2 都说明统计关系不成立。
                if not (0.5 <= a <= 2.0):
                    logging.warning(f"[mz5_11] col={col} 的 a={a:.3f} 超出 [0.5, 2.0]，跳过")
                    continue

                # 公式(12)-(14)：均值位移差 c
                x_T_aligned = _shift_signal(x_ref, lag)[T_slice]
                c = _solve_c(a, x_T_aligned, y_T, S_y)

                result[col] = {
                    'x_ref': x_ref,
                    'lag': int(lag),
                    'a': a,
                    'c': float(c),
                    'k': int(k),
                }
                logging.info(
                    f"[mz5_11 标定] col={col}: lag={lag}, a={a:.6f}, "
                    f"c={c:.3e}, k={k}, |T|={len(range(*T_slice.indices(n_time)))}"
                )
            except Exception as e:
                logging.warning(f"[mz5_11 预处理] col={col} 失败: {e}")

        return result


    def print_stats(self, stats: Optional[DenoisingStats] = None) -> None:
        """
        打印处理统计信息
        """

        if stats is None and self.stats_history:
            stats = self.stats_history[-1]
        elif stats is None:
            print("没有可用的统计信息")
            return

        print(stats.summary())

        print(f"\n详细统计 (前10列):")

        count = 0
        for col, detail in stats.column_details.items():
            if count >= 10:
                print("  ...")
                break
            print(f"  {col}: {detail['status']}", end="")
            if detail['status'] == 'processed':
                print(f" ({detail['method']})")
            elif detail['status'] == 'error':
                print(f" - {detail.get('error', '未知错误')}")
            else:
                print(f" - {detail.get('reason', '')}")
            count += 1

# %% ==================== 智能配置生成器类（xxxxxx） ====================
class SmartConfigGenerator:
    """智能配置生成器"""

    def __init__(self, df: pd.DataFrame):
        """
        初始化智能配置生成器

        Args:
            df: 数据框
        """
        self.df = df
        self.column_stats: Dict[str, Dict] = {}

    def analyze_columns(self) -> Dict[str, Dict]:
        """
        分析所有列的统计特性
        """
        for col in self.df.columns:
            series = self.df[col].to_numpy()    # self.df[col] 是一个 pandas Series（一维带标签的数组），
                                            # 而 .values 属性返回的是该 Series 底层的 NumPy 数组 (ndarray)。

            self.column_stats[col] = {
                'mean': np.mean(series),
                'std': np.std(series),
                'max': np.max(series),
                'min': np.min(series),
                'snr': abs(np.mean(series)) / (np.std(series) + 1e-20),
                'peak_count': self._count_peaks(series)
            }

        return self.column_stats.copy()

    def _count_peaks(self, series: np.ndarray, prominence: Optional[float] = None) -> int:
        """
        计算峰数量:
        方法用于计算一维数组（信号）中显著峰的数量,
        使用了 SciPy 的 find_peaks 函数，并通过 prominence 参数控制峰的显著性阈值。

        prominence：峰的突出度阈值。若为 None，则自动设为信号标准差的一半（np.std(series) * 0.5）,
        这是一种启发式方法，能根据信号的波动幅度自动调整灵敏度：噪声大的信号阈值更高，只检出更显著的峰。
        """
        if prominence is None:
            prominence = np.std(series) * 0.5

        try:
            '''
            prominence 参数要求每个峰必须至少达到指定的突出度（峰顶与左右最低轮廓线的垂直距离），能有效抑制噪声引起的假峰。
            peaks 是峰位置的索引数组；_ 是包含峰属性（如突出度、宽度）的字典，这里未被使用。
            '''
            peaks, _ = find_peaks(series, prominence=prominence)
            return len(peaks)
        except Exception:
            return 0    # 捕获任何异常（例如 series 为空、SciPy 未安装、find_peaks 调用失败），返回 0。

    def generate_by_snr(self, snr_thresholds: List[float] = None,
                    config_map: Optional[Dict[str, Tuple[str, Dict]]] = None,
                    interval_order: Optional[List[str]] = None) -> DenoiseConfigManager:
        """
        根据信噪比自动生成配置（支持自定义阈值和映射）
        更好的做法是在计算 SNR 之前，先确保信号已经过预处理（如基线扣除、去趋势等）***

        Args:
            snr_thresholds: 信噪比阈值列表（升序），例如 [1, 3, 5]。默认为 [1, 3, 5]
            config_map: 字典，键为区间名称（'low','medium','high','very_high'），
                    值为 (method, params) 元组。若为 None，使用默认映射。
            interval_order: 区间名称列表，按优先级排序。若为 None，按默认顺序 ['low', 'medium', 'high', 'very_high']。

        Returns:
        配置管理器（新实例）

        默认映射:
            - low:      ('wavelet', {'wavelet': 'db4', 'level': 3, 'threshold_type': 'hard'})
            - medium:   ('savitzky_golay', {'window_size': 11, 'polyorder': 3})
            - high:     ('median', {'window_size': 5})
            - very_high:('ewma', {'alpha': 0.5})
        """
        # 1. 设置默认阈值
        if snr_thresholds is None:
            snr_thresholds = [1, 3, 5]

        # 2. 设置默认配置映射
        # 低信噪时使用 wavelet，兼顾去噪强度与稳定性；如需使用自研方法，
        # 可显式传入 config_map 并指定 'mz5_11'（需确保数据包含 m/z 5-11 通道）
        if config_map is None:
            config_map = {
                'low':       ('wavelet', {'wavelet': 'db4', 'level': 3, 'threshold_type': 'hard'}),
                'medium':    ('savitzky_golay', {'window_size': 11, 'polyorder': 3}),
                'high':      ('median', {'window_size': 5}),
                'very_high': ('ewma', {'alpha': 0.5})
            }

        #  设置默认区间顺序
        if interval_order is None:
            interval_order = list(config_map.keys())

        # 关键校验：阈值数量必须与区间数量匹配，20260912
        expected_intervals = len(snr_thresholds) + 1
        if len(interval_order) != expected_intervals:
            raise ValueError(
                f"interval_order 数量 ({len(interval_order)}) 必须等于 "
                f"snr_thresholds 数量 + 1 ({expected_intervals})。"
                f"snr_thresholds={snr_thresholds}, interval_order={interval_order}"
            )
        for key in interval_order:
            if key not in config_map:
                raise KeyError(f"interval_order 中的 '{key}' 不在 config_map 中")

        # 3. 确保有列统计信息
        if not self.column_stats:
            self.analyze_columns()

        # 4. 创建新的配置管理器
        config_manager = DenoiseConfigManager()

        # 5. 统计各区间列数（用于打印）
        count_stats = {key: 0 for key in config_map.keys()}

        # 6. 遍历所有列，根据 SNR 分档
        for col, stats in self.column_stats.items():
            snr = stats['snr']
            # 确定所属区间
            assigned = False
            for i, thresh in enumerate(snr_thresholds):
                if snr < thresh:
                    # 根据索引选择区间键（例如 low, medium, high）
                    # 这里假设阈值个数 <= config_map 的键个数
                    key = list(config_map.keys())[i]
                    method, params = config_map[key]
                    config_manager.set_column_config(col, method, copy.deepcopy(params) if params else {})
                    count_stats[key] += 1
                    assigned = True
                    break

            if not assigned:
                # 高于所有阈值，使用最后一个区间（如 very_high）
                key = list(config_map.keys())[-1]
                method, params = config_map[key]
                config_manager.set_column_config(col, method, copy.deepcopy(params) if params else {})
                count_stats[key] += 1

        # 7. 打印统计信息
        print(f"基于 SNR 阈值为 {snr_thresholds} 生成了配置，共 {len(config_manager.column_configs)} 列：")
        for key, cnt in count_stats.items():
            if cnt > 0:
                print(f"  - {key}: {cnt} 列")

        return config_manager

    def generate_by_intensity(self, intensity_thresholds: List[float] = None,
                              config_map: Optional[Dict[str, Tuple[str, Dict]]] = None,
                              interval_order: Optional[List[str]] = None) -> DenoiseConfigManager:
        """
        根据平均强度自动生成配置（支持自定义阈值和映射）

        Args:
            intensity_thresholds: 强度阈值列表（升序），例如 [1e-12, 1e-10, 1e-8]。默认为 [1e-12, 1e-10, 1e-8]
            config_map: 字典，键为区间名称（'low','medium','high','very_high'），
                        值为 (method, params) 元组。若为 None，使用默认映射。

        Returns:
            配置管理器（新实例）

        默认映射:
            - low:      ('std_threshold', {'window_size': 11, 'sigma_multiple': 3})  # 低强度，可能是噪声
            - medium:   ('median', {'window_size': 7})
            - high:     ('wavelet', {'wavelet': 'db4', 'level': 2, 'threshold_type': 'soft'})
            - very_high:('ewma', {'alpha': 0.7})
        """
        # 1. 设置默认阈值
        if intensity_thresholds is None:
            intensity_thresholds = [1e-12, 1e-10, 1e-8]

        # 2. 设置默认配置映射
        if config_map is None:
            config_map = {
                'low':       ('std_threshold', {'window_size': 11, 'sigma_multiple': 3}),
                'medium':    ('median', {'window_size': 7}),
                'high':      ('wavelet', {'wavelet': 'db4', 'level': 2, 'threshold_type': 'soft'}),
                'very_high': ('ewma', {'alpha': 0.7})
            }

        if interval_order is None:
            interval_order = list(config_map.keys())

        # 3. 校验：N 个阈值 → N+1 个区间
        expected_intervals = len(intensity_thresholds) + 1
        if len(interval_order) != expected_intervals:
            raise ValueError(
                f"interval_order 数量 ({len(interval_order)}) 必须等于 "
                f"intensity_thresholds 数量 + 1 ({expected_intervals})。"
                f"intensity_thresholds={intensity_thresholds}, "
                f"interval_order={interval_order}"
            )
        for key in interval_order:
            if key not in config_map:
                raise KeyError(f"interval_order 中的 '{key}' 不在 config_map 中")

        # 4. 确保有列统计信息
        if not self.column_stats:
            self.analyze_columns()

        # 5. 创建新的配置管理器
        config_manager = DenoiseConfigManager()

        # 6. 统计各区间列数
        count_stats = {key: 0 for key in interval_order}

        # 7. 遍历所有列，根据平均强度分档
        for col, stats in self.column_stats.items():
            mean_intensity = stats['mean']

            assigned = False
            for i, thresh in enumerate(intensity_thresholds):
                if mean_intensity < thresh:
                    key = list(config_map.keys())[i]
                    method, params = config_map[key]
                    config_manager.set_column_config(col, method, copy.deepcopy(params) if params else {})
                    count_stats[key] += 1
                    assigned = True
                    break

            if not assigned:
                # 强度高于所有阈值，使用最后一个区间（如 very_high）
                key = list(config_map.keys())[-1]
                method, params = config_map[key]
                config_manager.set_column_config(col, method, copy.deepcopy(params) if params else {})
                count_stats[key] += 1

        # 7. 打印统计信息
        print(f"基于强度阈值为 {intensity_thresholds} 生成了配置，共 {len(config_manager.column_configs)} 列：")
        for key, cnt in count_stats.items():
            if cnt > 0:
                print(f"  - {key}: {cnt} 列")

        return config_manager

# %% ==================== 可视化类 ====================
class DenoiseVisualizer:
    """
    去噪可视化器：
    plot_comparison: 对比显示原始信号与去噪后信号的波形图。
    plot_stats_summary：以柱状图形式展示多个统计特征（均值、标准差、信噪比）的分布。
    """

    def __init__(self, figsize: Tuple[int, int] = (14, 8)):
        """
        初始化可视化器

        Args:
            figsize: 图形大小
        """
        self.figsize = figsize

    def plot_3d_comparison(self, df_original: pd.DataFrame, df_denoised: pd.DataFrame,
                           mz_range: Optional[Tuple[float, float]] = None,
                           time_range: Optional[Tuple[int, int]] = None,
                           stride: int = 1, save_path: Optional[str] = None,
                           normalize_z: bool = True,
                           max_points_per_axis: int = 100) -> None:
        """
        绘制原始数据与去噪后数据的三维对比曲面图（1行2列）

        Args:
            df_original: 原始数据框，列名为 m/z 值（字符串），行索引为时间/扫描号
            df_denoised: 去噪后数据框，结构同 df_original
            mz_range: (min_mz, max_mz) 可选，限制显示的 m/z 范围
            time_range: (start, end) 可选，限制显示的时间范围
            stride: 步长，用于下采样（减少数据点数量，提高绘图速度），默认为1
            save_path: 保存路径，若提供则保存图片而不显示
             normalize_z: 是否将 Z 轴数据归一化到 [0,1] 范围（推荐开启，避免数值过大导致绘图警告）
        """
        # 预先构建列名 → m/z 数值的映射（只解析一次）,并记录有效列名
        col_mz_map = {}
        valid_columns = []
        skipped_cols = []
        for col in df_original.columns:
            val = DenoiseConfigManager._extract_mz_from_column(col)
            if val is not None:
                col_mz_map[col] = val
                valid_columns.append(col)
            elif DenoiseConfigManager._is_time_column(col):
                # 时间 / 索引 / 元数据列：静默跳过，不打警告
                skipped_cols.append(col)
            else:
                # 真正无法识别的列才提示
                print(f"警告：列 '{col}' 无法提取 m/z 数值，将在三维图中忽略")

        if skipped_cols:
            print(f"提示：已跳过非 m/z 列（时间/索引/元数据）：{skipped_cols}")

        if not valid_columns:
            print("错误：没有有效列可用于三维绘图，请检查数据框列名")
            return

        # 定义 get_mz 函数（此时所有列都在映射中）
        def get_mz(col: str) -> float:
            return col_mz_map[col]   # 不再需要返回 inf，因为列都已验证

        # 1. 准备数据：筛选时间范围和 m/z 范围
        # 仅保留有效列
        data_orig = df_original[valid_columns].copy()
        data_den = df_denoised[valid_columns].copy()

        if time_range is not None:
            start, end = time_range
            data_orig = data_orig.iloc[start:end]
            data_den = data_den.iloc[start:end]

        if mz_range is not None:
            min_mz, max_mz = mz_range
            selected_cols = [col for col in data_orig.columns
                             if min_mz <= get_mz(col) <= max_mz]
            if not selected_cols:
                print("警告: 指定 m/z 范围内无有效列，显示全部列")
            else:
                data_orig = data_orig[selected_cols]
                data_den = data_den[selected_cols]

        # 2. 提取 X (时间) 和 Y (m/z) 坐标，并下采样
        X = np.arange(data_orig.shape[0])               # 行数 = 时间点数
        mz_values = [get_mz(col) for col in data_orig.columns]
        Y = np.array(mz_values)

        # 若未指定 stride，自动计算，20260912
        if stride is None or stride < 1:
            n_rows = df_original.shape[0]
            n_cols = df_original.shape[1]
            stride = max(1, int(max(n_rows / max_points_per_axis,
                                    n_cols / max_points_per_axis)))
            # 确保至少保留 2 个点
            while stride > 1 and (n_rows // stride < 2 or n_cols // stride < 2):
                stride -= 1
            print(f"[plot_3d_comparison] 自动 stride = {stride}")

        # 下采样（步长）
        X = X[::stride]
        Y = Y[::stride]
        Z_orig = data_orig.values[::stride, ::stride]   # 同时下采样行和列
        Z_den = data_den.values[::stride, ::stride]

        # 清理无效值
        Z_orig = np.nan_to_num(Z_orig, nan=0.0, posinf=0.0, neginf=0.0)
        Z_den = np.nan_to_num(Z_den, nan=0.0, posinf=0.0, neginf=0.0)

        # 检查数据点是否足够
        if len(X) < 2 or len(Y) < 2:
            print("警告：下采样后数据点不足（行数或列数<2），无法绘制三维曲面")
            return

        # 归一化处理（关键改进）
        if normalize_z:
            # 使用原始和去噪后数据的全局最大值进行归一化
            global_max = max(Z_orig.max(), Z_den.max())
            if global_max > 0:
                Z_orig = Z_orig / global_max
                Z_den = Z_den / global_max
                print(f"注意：Z 轴数据已归一化到 [0,1] 范围（原始最大值 = {global_max:.2e}）")


        print("X range:", X.min(), X.max())
        print("Y range:", Y.min(), Y.max())
        print("Z_orig range:", Z_orig.min(), Z_orig.max())
        print("Z_den range:", Z_den.min(), Z_den.max())

        # 创建网格
        X_grid, Y_grid = np.meshgrid(X, Y, indexing='ij')

        # 3. 创建子图
        fig = plt.figure(figsize=self.figsize)
        ax1 = fig.add_subplot(1, 2, 1, projection='3d')
        ax2 = fig.add_subplot(1, 2, 2, projection='3d')

        # 绘制原始数据曲面
        surf1 = ax1.plot_surface(X_grid, Y_grid, Z_orig, cmap='viridis',
                                 linewidth=0, antialiased=True, alpha=0.8)
        ax1.set_title('Original Data')
        ax1.set_xlabel('Scan Number')
        ax1.set_ylabel('m/z')
        ax1.set_zlabel('Intensity')
        fig.colorbar(surf1, ax=ax1, shrink=0.5, aspect=10)

        # 绘制去噪后数据曲面
        surf2 = ax2.plot_surface(X_grid, Y_grid, Z_den, cmap='plasma',
                                 linewidth=0, antialiased=True, alpha=0.8)
        ax2.set_title('Denoised Data')
        ax2.set_xlabel('Scan Number')
        ax2.set_ylabel('m/z')
        ax2.set_zlabel('Intensity')
        fig.colorbar(surf2, ax=ax2, shrink=0.5, aspect=10)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"三维对比图已保存至 {save_path}")
            plt.close(fig)    # 修复：保存后关闭图形，释放内存
        else:
            plt.show()

    def plot_comparison(self, df_original: pd.DataFrame, df_denoised: pd.DataFrame,
                       columns: List[str], time_range: Optional[Tuple[int, int]] = None,
                       max_plots: int = 5, save_path: Optional[str] = None) -> None:
        """
        绘制二维去噪效果对比图(按 mz 列对比)

        Args:
            df_original: 原始数据框
            df_denoised: 去噪后数据框
            columns: 要显示的列列表
            time_range: 时间范围 (起始, 结束)。超出数据长度时自动截断到有效区间。
            max_plots: 最大显示列数
            save_path: 保存路径，None 表示不保存
        """

        # 1. 过滤出实际存在的列（且限制数量）
        existing_cols = [col for col in columns[:max_plots]
                         if col in df_original.columns and col in df_denoised.columns]
        n_cols = len(existing_cols)

        if n_cols == 0:
            print("没有列可显示（列不存在或数据框中无此列）")
            return

        # 2. 根据实际列数动态创建子图
        fig, axes = plt.subplots(n_cols, 2, figsize=(self.figsize[0], 3 * n_cols))    # n_cols即n_rows，3为3英寸高度

        if n_cols == 1:
            axes = axes.reshape(1, -1)    # 确保 axes 始终为二维数组

        # 3. 循环绘制每个存在的列
        for i, col in enumerate(existing_cols):
            if col not in df_original.columns or col not in df_denoised.columns:
                continue

            original_series = df_original[col].values
            denoised_series = df_denoised[col].values

            # 限制时间范围
            if time_range:
                start, end = time_range
                n = len(original_series)          # ★ 先取长度
                start = max(0, start)             # ★ 先处理 start
                end = min(end, n)                 # ★ 再处理 end
                if start >= end:
                    logging.warning("[plot_comparison] time_range=(%d,%d) 无效，跳过列 %s",
                                    start, end, col)
                    axes[i, 0].text(0.5, 0.5, 'invalid time_range',
                                    ha='center', va='center',
                                    color='red', fontsize=12,
                                    transform=axes[i, 0].transAxes)
                    axes[i, 1].text(0.5, 0.5, 'invalid time_range',
                                    ha='center', va='center',
                                    color='red', fontsize=12,
                                    transform=axes[i, 1].transAxes)
                    continue
                original_series = original_series[start:end]
                denoised_series = denoised_series[start:end]
                time = np.arange(start, end)
            else:
                # 生成从 start 到 end-1 的整数序列作为时间轴，np.arange 直接生成 NumPy 数组（存储在内存中），
                # 支持整数和浮点数，且支持向量化运算。
                time = np.arange(len(original_series))

            # 原始数据（左图）
            axes[i, 0].plot(time, original_series, label='Original', alpha=0.7, color='blue')
            axes[i, 0].set_xlabel('Time (Scan Number)')
            axes[i, 0].set_ylabel('Intensity')
            axes[i, 0].set_title(f'Original - m/z={col}')
            axes[i, 0].legend()
            axes[i, 0].grid(True, alpha=0.3)

            # 去噪后数据（右图）
            axes[i, 1].plot(time, denoised_series, label='Denoised', alpha=0.7, color='red')
            axes[i, 1].set_xlabel('Time (Scan Number)')
            axes[i, 1].set_ylabel('Intensity')
            axes[i, 1].set_title(f'After Denoising - m/z={col}')
            axes[i, 1].legend()
            axes[i, 1].grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"二维对比图已保存至 {save_path}")
            plt.close(fig)    # 修复：保存后关闭图形，释放内存
        else:
            plt.show()

    def plot_row_comparison(self, df_original: pd.DataFrame, df_denoised: pd.DataFrame,
                            rows: Optional[List[int]] = None,
                            mz_range: Optional[Tuple[float, float]] = None,
                            max_plots: int = 5,
                            overlay: bool = False,
                            save_path: Optional[str] = None) -> None:
        """
        绘制按行（时间/扫描点）的去噪效果对比图：新增，20260912
        每个子图展示某一行（某个扫描时间点）在所有 m/z 上的光谱曲线，
        左右分列对比"原始 vs 去噪"；横轴为 m/z，纵轴为强度。

        Args:
            df_original:  原始数据框，列名需可解析为 m/z
            df_denoised:  去噪后数据框，结构同 df_original
            rows:         要显示的行索引（时间/扫描号）列表；None 表示自动均匀选取 max_plots 行
            mz_range:     (min_mz, max_mz) 可选，限制 m/z 显示范围
            max_plots:    最多显示的行数
            overlay:      True 表示将原始与去噪曲线叠加在同一子图对比；
                          False（默认）表示左右并排显示
            save_path:    保存路径；若提供则保存图片而不显示
        """
        # ---------- 1. 提取有效 m/z 列（跳过时间/索引/元数据列） ----------
        col_mz_map: Dict[str, float] = {}
        valid_columns: List[str] = []
        skipped_cols: List[str] = []
        for col in df_original.columns:
            val = DenoiseConfigManager._extract_mz_from_column(col)
            if val is not None:
                col_mz_map[col] = val
                valid_columns.append(col)
            elif DenoiseConfigManager._is_time_column(col):
                skipped_cols.append(col)
            else:
                print(f"警告：列 '{col}' 无法提取 m/z 数值，将在行对比图中忽略")

        if skipped_cols:
            print(f"提示：已跳过非 m/z 列（时间/索引/元数据）：{skipped_cols}")

        if not valid_columns:
            print("错误：没有有效列可用于行对比绘图，请检查数据框列名")
            return

        # 按 m/z 升序排列，使光谱曲线沿横轴自然递增
        valid_columns = sorted(valid_columns, key=lambda c: col_mz_map[c])
        mz_values = np.array([col_mz_map[c] for c in valid_columns], dtype=float)

        # ---------- 2. 按 m/z 范围过滤列 ----------
        if mz_range is not None:
            min_mz, max_mz = mz_range
            mask = (mz_values >= min_mz) & (mz_values <= max_mz)
            if mask.sum() == 0:
                print("警告：指定 m/z 范围内无有效列，将显示全部列")
            else:
                valid_columns = [c for c, m in zip(valid_columns, mask) if m]
                mz_values = mz_values[mask]

        n_rows_total = len(df_original)

        # ---------- 3. 选择要显示的行 ----------
        if rows is None:
            if n_rows_total <= max_plots:
                rows_to_plot = list(range(n_rows_total))
            else:
                # 均匀采样 max_plots 行（含首尾）
                rows_to_plot = np.linspace(0, n_rows_total - 1,
                                           max_plots, dtype=int).tolist()
        else:
            rows_to_plot = [r for r in rows if 0 <= r < n_rows_total][:max_plots]

        if not rows_to_plot:
            print("没有有效的行可显示（rows 为空或索引越界）")
            return

        n_plots = len(rows_to_plot)

        # ---------- 4. 绘图 ----------
        if overlay:
            # 叠加模式：每行一个子图，原始/去噪曲线同图对比
            fig, axes = plt.subplots(n_plots, 1,
                                     figsize=(self.figsize[0], 3 * n_plots))
            if n_plots == 1:
                axes = np.array([axes])
            for i, row_idx in enumerate(rows_to_plot):
                orig_series = df_original.iloc[row_idx][valid_columns].astype(float).values
                den_series  = df_denoised.iloc[row_idx][valid_columns].astype(float).values

                axes[i].plot(mz_values, orig_series,
                             label='Original', alpha=0.6, color='blue', linewidth=0.8)
                axes[i].plot(mz_values, den_series,
                             label='Denoised', alpha=0.9, color='red', linewidth=1.0)
                axes[i].set_xlabel('m/z')
                axes[i].set_ylabel('Intensity')
                axes[i].set_title(f'Row/Scan {row_idx}  (Original vs Denoised)')
                axes[i].legend()
                axes[i].grid(True, alpha=0.3)
        else:
            # 并排模式：左=原始，右=去噪
            fig, axes = plt.subplots(n_plots, 2,
                                     figsize=(self.figsize[0], 3 * n_plots))
            if n_plots == 1:
                axes = axes.reshape(1, -1)
            for i, row_idx in enumerate(rows_to_plot):
                orig_series = df_original.iloc[row_idx][valid_columns].astype(float).values
                den_series  = df_denoised.iloc[row_idx][valid_columns].astype(float).values

                # 原始（左）
                axes[i, 0].plot(mz_values, orig_series,
                                label='Original', alpha=0.7, color='blue', linewidth=0.8)
                axes[i, 0].set_xlabel('m/z')
                axes[i, 0].set_ylabel('Intensity')
                axes[i, 0].set_title(f'Original - Row/Scan {row_idx}')
                axes[i, 0].legend()
                axes[i, 0].grid(True, alpha=0.3)

                # 去噪（右）
                axes[i, 1].plot(mz_values, den_series,
                                label='Denoised', alpha=0.7, color='red', linewidth=0.8)
                axes[i, 1].set_xlabel('m/z')
                axes[i, 1].set_ylabel('Intensity')
                axes[i, 1].set_title(f'After Denoising - Row/Scan {row_idx}')
                axes[i, 1].legend()
                axes[i, 1].grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"行对比图已保存至 {save_path}")
            plt.close(fig)    # 修复：保存后关闭图形，释放内存
        else:
            plt.show()

    def plot_stats_summary(self, df: pd.DataFrame, columns: Optional[List[str]] = None,
                          n_columns: int = 10, save_path: Optional[str] = None) -> None:
        """
        绘制列统计摘要图

        Args:
            df: 数据框
            columns: 要显示的列列表，None表示显示前n_columns列
            n_columns: 显示列数
        """
        if columns is None:
            columns = df.columns[:n_columns].tolist()

        # 计算统计，并记录实际存在的列名
        valid_columns = []
        means = []
        stds = []
        snrs = []

        for col in columns:
            if col in df.columns:
                series = df[col].values
                mean_val = np.mean(series)
                std_val = np.std(series)

                valid_columns.append(col)
                means.append(mean_val)
                stds.append(std_val)
                snrs.append(mean_val / (std_val + 1e-20))

        if len(valid_columns) == 0:
            print("没有有效的列可显示")
            return

        fig, axes = plt.subplots(1, 3, figsize=(15, 5))

        # 生成刻度位置和标签（每隔 step 个显示一个）
        n_valid = len(valid_columns)
        step = max(1, n_valid // 5)
        tick_positions = list(range(0, n_valid, step))
        tick_labels = [valid_columns[i] for i in tick_positions]
        # 确保最后一个标签也被包含（如果步长未覆盖）
        if tick_positions[-1] != n_valid - 1 and n_valid > 1:
            tick_positions.append(n_valid - 1)
            tick_labels.append(valid_columns[-1])

        # 均值分布
        axes[0].bar(range(len(means)), means, alpha=0.7)
        axes[0].set_xlabel('Column Name')
        axes[0].set_ylabel('Mean Intensity')
        axes[0].set_title('Mean Intensity Distribution')
        axes[0].set_xticks(tick_positions)
        axes[0].set_xticklabels(tick_labels, rotation=45, ha='right', fontsize=8)
        axes[0].grid(True, alpha=0.3)

        # 标准差分布
        axes[1].bar(range(len(stds)), stds, alpha=0.7, color='orange')
        axes[1].set_xlabel('Column Name')
        axes[1].set_ylabel('Standard Deviation')
        axes[1].set_title('Noise Level Distribution')
        axes[1].set_xticks(tick_positions)
        axes[1].set_xticklabels(tick_labels, rotation=45, ha='right', fontsize=8)
        axes[1].grid(True, alpha=0.3)

        # 信噪比分布
        axes[2].bar(range(len(snrs)), snrs, alpha=0.7, color='green')
        axes[2].set_xlabel('Column Name')
        axes[2].set_ylabel('SNR')
        axes[2].set_title('Signal-to-Noise Ratio Distribution')
        axes[2].set_xticks(tick_positions)
        axes[2].set_xticklabels(tick_labels, rotation=45, ha='right', fontsize=8)
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches='tight')
            print(f"统计图已保存至 {save_path}")
            plt.close(fig)    # 修复：保存后关闭图形，释放内存
        else:
            plt.show()

# %% ==================== 特殊应用类 ====================
class IsotopePairProcessor:
    """同位素对处理器"""

    def __init__(self, default_spacing: float = 1.003355):
        """
        初始化同位素对处理器

        Args:
            default_spacing: 默认同位素间距
        """
        self.default_spacing = default_spacing

    def process(self, df: pd.DataFrame, base_mz: float,
                isotope_spacing: Optional[float] = None
                ) -> Tuple[pd.DataFrame, List[str], float, DenoisingStats]:
        """
        处理同位素对。

        Args:
            df: 数据框
            base_mz: 基峰 m/z 值
            isotope_spacing: 同位素间距，None 表示使用默认值

        Returns:
            (去噪后数据框, 同位素对列列表, 同位素比例, 处理统计信息)
        """
        if isotope_spacing is None:
            isotope_spacing = self.default_spacing

        isotope_mz = base_mz + isotope_spacing

        # 关键修复：先排除时间/索引/元数据列，再构建 m/z 映射
        col_mz_map: Dict[str, float] = {}
        for col in df.columns:
            if DenoiseConfigManager._is_time_column(col):
                continue
            val = DenoiseConfigManager._extract_mz_from_column(col)
            if val is not None:
                col_mz_map[col] = val

        if not col_mz_map:
            raise ValueError("数据框中没有任何可解析 m/z 的列，无法进行同位素对匹配")

        def find_closest(target: float) -> Tuple[str, float]:
            closest = min(col_mz_map, key=lambda c: abs(col_mz_map[c] - target))
            return closest, abs(col_mz_map[closest] - target)

        base_col, base_error = find_closest(base_mz)
        isotope_col, isotope_error = find_closest(isotope_mz)

        if base_error > 0.1 or isotope_error > 0.1:
            print(f"警告: 同位素对匹配不精确")
            print(f"  基峰:   {base_col} (目标: {base_mz}, 误差: {base_error:.4f})")
            print(f"  同位素: {isotope_col} (目标: {isotope_mz}, 误差: {isotope_error:.4f})")

        # 为同位素对设置相同配置
        config_manager = DenoiseConfigManager()
        config_manager.set_column_config(
            base_col, 'savitzky_golay', {'window_size': 9, 'polyorder': 3})
        config_manager.set_column_config(
            isotope_col, 'savitzky_golay', {'window_size': 9, 'polyorder': 3})

        denoiser = SelectiveDenoiser(config_manager)
        denoised_df, stats = denoiser.denoise(df)

        base_intensity = denoised_df[base_col].mean()
        isotope_intensity = denoised_df[isotope_col].mean()
        ratio = isotope_intensity / base_intensity if base_intensity > 0 else 0.0

        print(f"同位素比例 (去噪后): {ratio:.3f}")

        return denoised_df, [base_col, isotope_col], ratio, stats

class BiomarkerProcessor:
    """生物标志物处理器"""

    def __init__(self):
        """初始化生物标志物处理器"""
        pass

    def process(self, df: pd.DataFrame, biomarker_list: List[Dict]
                ) -> Tuple[pd.DataFrame, DenoisingStats]:
        """
        处理生物标志物面板。

        Args:
            df: 数据框
            biomarker_list: 生物标志物列表，每个元素为
                            {'mz': ..., 'method': ..., 'params': ...}

        Returns:
            (去噪后数据框, 处理统计信息)
        """
        config_manager = DenoiseConfigManager()

        # 关键修复：先排除时间/索引/元数据列，再构建 m/z 映射
        col_mz_map: Dict[str, float] = {}
        for col in df.columns:
            if DenoiseConfigManager._is_time_column(col):
                continue
            val = DenoiseConfigManager._extract_mz_from_column(col)
            if val is not None:
                col_mz_map[col] = val

        if not col_mz_map:
            raise ValueError("数据框中没有任何可解析 m/z 的列，无法进行生物标志物匹配")

        def find_closest(target: float) -> Tuple[str, float]:
            closest = min(col_mz_map, key=lambda c: abs(col_mz_map[c] - target))
            return closest, abs(col_mz_map[closest] - target)

        for biomarker in biomarker_list:
            mz = biomarker['mz']
            method = biomarker.get('method', 'wavelet')
            params = biomarker.get('params', {})

            closest, error = find_closest(mz)
            if error < 0.1:
                config_manager.set_column_config(closest, method, params)
                print(f"  生物标志物: m/z={mz} -> {closest} (误差: {error:.4f})")
            else:
                print(f"  警告: 生物标志物 m/z={mz} 未找到合适匹配 "
                    f"(最近: {closest}, 误差: {error:.4f})")

        denoiser = SelectiveDenoiser(config_manager)
        denoised_df, stats = denoiser.denoise(df)

        return denoised_df, stats

# %% ==================== 工作流程管理器类 ====================
class SelectiveDenoisingWorkflow:
    """选择性去噪工作流程管理器"""
    def __init__(self, df: pd.DataFrame, config_manager: Optional[DenoiseConfigManager] = None):
        """
        初始化工作流程管理器

        Args:
            df: 数据框
        """
        self.df = df
        self.config_manager = config_manager or DenoiseConfigManager()
        self.denoiser: Optional[SelectiveDenoiser] = None
        self.visualizer = DenoiseVisualizer()
        self.smart_generator = SmartConfigGenerator(df)

    def run(self, manual_config: Optional[Dict[str, Dict[str, Any]]] = None,
           auto_generate: Optional[str] = 'snr',
           visualize: bool = True,
           save_plot: Optional[str] = None,
           time_range: Optional[Tuple[int, int]] = None) -> Tuple[pd.DataFrame, DenoiseConfigManager, DenoisingStats]:
        """
        运行完整工作流程

        Args:
            manual_config: 手动配置字典
            auto_generate: 自动生成配置的方法，'snr'、'intensity'或None

        Returns:
            (去噪后数据框, 配置管理器, 处理统计信息)
        """
        print("=" * 80)
        print("选择性去噪工作流程")
        print("=" * 80)

        # 步骤1: 配置管理器
        if self.config_manager is None:
            self.config_manager = DenoiseConfigManager(default_method='savitzky_golay')

        # 步骤2: 应用手动配置
        if manual_config:
            self.config_manager.set_multiple_columns(manual_config)
            print(f"已应用手动配置: {len(manual_config)} 列")

        # 步骤3: 自动生成配置
        # 校验 auto_generate 参数
        valid_modes = {'snr', 'intensity', None}
        if auto_generate not in valid_modes:
            raise ValueError(f"auto_generate 必须是 {valid_modes} 之一，得到 {auto_generate}")

        if auto_generate == 'snr':
            auto_config = self.smart_generator.generate_by_snr()
            # 合并配置（手动配置优先）
            for col, config in auto_config.column_configs.items():
                if col not in self.config_manager.column_configs:
                    self.config_manager.set_column_config(col, config.method, config.params)
            print(f"已应用基于SNR的自动配置")

        elif auto_generate == 'intensity':
            auto_config = self.smart_generator.generate_by_intensity()
            # 合并配置（手动配置优先）
            for col, config in auto_config.column_configs.items():
                if col not in self.config_manager.column_configs:
                    self.config_manager.set_column_config(col, config.method, config.params)
            print(f"已应用基于强度的自动配置")

        # 步骤4: 显示配置摘要
        self.config_manager.print_summary()

        # 步骤5: 创建并运行去噪器
        self.denoiser = SelectiveDenoiser(self.config_manager)
        try:
            denoised_df, stats = self.denoiser.denoise(self.df, apply_to_all=False)
        except Exception as e:
            print(f"工作流程执行失败：{e}")
            raise    # 或返回部分结果

        # 步骤6: 打印处理统计
        self.denoiser.print_stats(stats)

        # 步骤7: 可视化
        configured_columns = list(self.config_manager.column_configs.keys())[:3]
            # 如果未指定 save_plot 路径，则自动保存到默认目录
        if visualize and (self.config_manager.column_configs):
            configured_columns = list(self.config_manager.column_configs.keys())[:3]
            print(f"\n可视化前3个配置列...")
            if save_plot is None:
                FIGURES_DIR.mkdir(parents=True, exist_ok=True)
                save_plot = str(FIGURES_DIR / 'comparison_plot.png')

            # 若未指定 time_range，则自动取全范围（带合理上限），20260912
            if time_range is None:
                n_points = len(self.df)
                time_range = (0, min(n_points, 2000)) if n_points > 2000 else None

            self.visualizer.plot_comparison(
                self.df, denoised_df, configured_columns,
                time_range=time_range,
                save_path=save_plot    # 传 None 则只显示，传路径则保存
            )
            plt.close('all')    # 关闭所有图形，释放内存

        return denoised_df, self.config_manager, stats

    def save_results(self, denoised_df: pd.DataFrame,
                    stats: DenoisingStats,
                    output_dir: Optional[str] = None,
                    db_dir: Optional[str] = None,
                    format: str = 'csv') -> Dict[str, str]:
        """
        保存结果：
            - 数据（CSV/Excel）→ output_dir（默认 DATA_DIR）
            - 配置（JSON）和报告（TXT）→ db_dir（默认 DB_DIR）

        ★ 输出文件名：denoised_data_ms.{csv|xlsx}
        ★ 输出前统一将 '##Time/min' 列名规范化为 'Time/min'

        Args:
            denoised_df: 去噪后的数据框
            stats: 处理统计信息
            output_dir: 输出目录（默认 DATA_DIR）
            db_dir: 数据库目录（默认 DB_DIR）
            format: 数据格式（'csv' 或 'excel'）
        """

        if self.config_manager is None or self.denoiser is None:
            raise RuntimeError("请先运行 run() 方法以初始化配置管理器和去噪器")

        if output_dir is None:
            output_dir = str(DATA_DIR)
        if db_dir is None:
            db_dir = str(DB_DIR)

        try:
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(db_dir, exist_ok=True)
        except IOError as e:
            print(f"保存失败: {e}")
            raise

        # ★ 时间列名规范化：确保输出 CSV/Excel 中不再出现 '##Time/min'
        denoised_df = _normalize_time_column(denoised_df)

        if format == 'csv':
            data_ext = '.csv'
            save_method = denoised_df.to_csv
        elif format == 'excel':
            data_ext = '.xlsx'
            save_method = denoised_df.to_excel
        else:
            raise ValueError("format 参数必须为 'csv' 或 'excel'")

        # ★ 输出文件名：denoised_data_ms.{csv|xlsx}（原 denoised_data.*）
        data_path = os.path.join(output_dir, f'denoised_data_ms{data_ext}')
        config_path = os.path.join(db_dir, 'denoising_config.json')
        report_path = os.path.join(db_dir, 'denoising_report.txt')

        save_method(data_path, index=False)

        if self.config_manager:
            self.config_manager.save_to_file(config_path)

        if self.denoiser and self.denoiser.stats_history:
            self._generate_report(report_path, denoised_df, stats)

        print(f"\n结果已保存:")
        print(f"  数据: {data_path}")
        print(f"  配置: {config_path}")
        print(f"  报告: {report_path}")

        return {
            'data_path': data_path,
            'config_path': config_path,
            'report_path': report_path,
        }

    def _generate_report(self, filepath: str, denoised_df: pd.DataFrame, stats: DenoisingStats) -> None:
        """生成去噪报告"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("质谱数据选择性去噪报告\n")
                f.write("=" * 80 + "\n\n")

                f.write("1. 数据概览\n")
                f.write(f"   原始数据形状: {self.df.shape}\n")
                f.write(f"   去噪数据形状: {denoised_df.shape}\n")
                f.write(f"   时间点数: {self.df.shape[0]}\n")
                f.write(f"   m/z列数: {self.df.shape[1]}\n\n")

                f.write("2. 处理统计\n")
                f.write(f"   总处理列数: {stats.total_columns}\n")
                f.write(f"   成功处理: {stats.processed}\n")
                f.write(f"   跳过处理: {stats.skipped}\n")
                f.write(f"   处理错误: {stats.errors}\n\n")

                f.write("3. 配置摘要\n")
                if self.config_manager:
                    config_dict = self.config_manager.to_dict()
                    f.write(f"   默认方法: {config_dict['default_method']}\n")
                    f.write(f"   配置列数: {len(config_dict['column_configs'])}\n\n")

                    # 新增：按方法统计，20260912
                    method_counts: Dict[str, int] = {}
                    for col_cfg in config_dict['column_configs'].values():
                        m = col_cfg['method']
                        method_counts[m] = method_counts.get(m, 0) + 1
                    f.write("   方法使用统计:\n")
                    for m, cnt in sorted(method_counts.items(),
                                        key=lambda kv: -kv[1]):
                        f.write(f"     {m}: {cnt} 列\n")
                    f.write("\n")

                f.write("4. 处理详情\n")
                f.write("   列名 | 状态 | 方法 | 备注\n")
                f.write("   " + "-" * 50 + "\n")

                for col, detail in list(stats.column_details.items())[:20]:
                    f.write(f"   {col} | {detail['status']} | {detail.get('method', 'N/A')} | {detail.get('reason', '')}\n")

                if len(stats.column_details) > 20:
                    f.write(f"   ... 还有 {len(stats.column_details) - 20} 列未显示\n")
        except Exception as e:
            print(f"警告：生成报告失败 - {e}")

        pass

# %% ==================== 批量处理器类（xxxxxx） ====================
class BatchDenoiseProcessor:
    """批量去噪处理器"""
    def __init__(self, config_template: Optional[Dict] = None):
        """
        初始化批量处理器

        Args:
            config_template: 【预留】配置模板，目前未在 process_files 中启用。
                            如需按 m/z 应用统一配置，请在调用前传入 config_manager。
        """
        self.config_template = config_template
        self.results: Dict[str, Dict] = {}

    def _copy_config_manager(self, config_manager: DenoiseConfigManager) -> DenoiseConfigManager:
        """复制配置管理器（调用其 copy 方法）"""
        return config_manager.copy()

    def process_files(self, file_list: List[str],
                      output_dir: str = None,
                      db_dir: str = None,
                      config_manager: Optional[DenoiseConfigManager] = None,
                      per_file_auto_config: bool = True
                     ) -> Dict[str, Dict]:
        """
        批量处理文件

        ★ 输出命名调整（与用户约定一致）：
          - 去噪数据 → denoised_data_batch_ms.csv
          - 去噪配置 → denoised_data_batch_ms.json
          若一次处理多个文件，为避免互相覆盖，将在文件名中加入序号索引。

        ★ 输出前统一将 '##Time/min' 列名规范化为 'Time/min'

        Args:
            file_list: 文件路径列表
            output_dir : 去噪数据的输出目录（默认 DATA_DIR）
            db_dir     : 配置 / 报告的保存目录（默认 DB_DIR）
            config_manager: 可选的配置管理器实例。若提供，则所有文件使用其配置（会复制后使用）。
            per_file_auto_config: 若为 True 且 config_manager 为 None，则对每个文件分别智能生成配置；
                                  若为 False，则使用空配置（只应用默认方法）。
        Returns:
            处理结果字典
        """

        # 1.解析默认目录
        if output_dir is None:
            output_dir = str(DATA_DIR)
        if db_dir is None:
            db_dir = str(DB_DIR)

        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(db_dir, exist_ok=True)
        results = {}

        # 若提供了配置管理器，则保存“母版”配置（后续复制使用）
        master_config = config_manager
        use_unified_config = master_config is not None

        # 处理多个文件时，为避免输出文件互相覆盖，加入序号索引
        multi_file_mode = len(file_list) > 1

        for file_idx, file_path in enumerate(file_list):
            print(f"\n处理文件 {file_idx+1}/{len(file_list)}: {file_path}")

            # 2. 加载数据
            try:
                if file_path.endswith('.csv'):
                    df = pd.read_csv(file_path)
                elif file_path.endswith('.xlsx'):
                    df = pd.read_excel(file_path)
                else:
                    print(f"跳过不支持的文件格式: {file_path}")
                    continue
            except Exception as e:
                print(f"加载文件失败: {e}")
                continue

            # ★ 统一时间列名：确保后续处理与输出 CSV 中均为 'Time/min'
            df = _normalize_time_column(df)

            # 3. 准备配置管理器（每个文件独立）
            if use_unified_config:
                # 复制母版配置，避免相互影响
                current_config = self._copy_config_manager(master_config)
            else:
                if per_file_auto_config:
                    # 基于当前文件智能生成配置
                    generator = SmartConfigGenerator(df)
                    current_config = generator.generate_by_snr()  # 可改为 generate_by_intensity
                else:
                    # 使用默认配置（所有列使用默认方法）
                    current_config = DenoiseConfigManager(default_method='savitzky_golay')

            # 4. 创建去噪器并执行去噪
            denoiser = SelectiveDenoiser(current_config)
            denoised_df, stats = denoiser.denoise(df, apply_to_all=True)

            # ★ 时间列名规范化（二次兜底，防止配置里携带了 '##Time/min'）
            denoised_df = _normalize_time_column(denoised_df)

            # 5. 保存结果
            # ★ 命名规则：
            #   - 单个文件：denoised_data_batch_ms.{csv|json}
            #   - 多个文件：denoised_data_batch_ms_{idx}.{csv|json}（避免覆盖）
            if multi_file_mode:
                stem = f"denoised_data_batch_ms_{file_idx + 1}"
            else:
                stem = "denoised_data_batch_ms"

            output_path = os.path.join(output_dir, f"{stem}.csv")
            config_path = os.path.join(output_dir, f"{stem}.json")

            denoised_df.to_csv(output_path, index=False)
            current_config.save_to_file(config_path)

            results[file_path] = {
                'output_path': output_path,
                'config_path': config_path,
                'processed_columns': stats.processed,
                'total_columns': stats.total_columns,
                'stats': stats,
                'denoised_df': denoised_df   # 新增
            }

            print(f"  结果保存到: {output_path}")
            print(f"  配置保存到: {config_path}")

        self.results = results    # 将局部结果赋值给实例属性
        return results

    def print_summary(self) -> None:
        """打印批量处理摘要"""
        print("\n" + "=" * 80)
        print("批量处理摘要")
        print("=" * 80)

        if not self.results:
            print("没有处理任何文件，请先运行 process_files() 方法。")
            return

        total_files = len(self.results)
        total_processed = sum(r['processed_columns'] for r in self.results.values())
        total_columns = sum(r['total_columns'] for r in self.results.values())

        print(f"总文件数: {total_files}")
        print(f"总处理列数: {total_processed} / {total_columns}")
        print(f"平均每文件处理列数: {total_processed / total_files:.1f}")

        print("\n文件详情:")
        for file_path, result in self.results.items():
            success_rate = result['processed_columns'] / result['total_columns'] * 100
            print(f"  {os.path.basename(file_path)}: {result['processed_columns']}/{result['total_columns']} 列 ({success_rate:.1f}%)")

        pass

# %% ==================== （CaCO3）实施去噪！！ ====================
def load_ms_data(file_path: Optional[str] = None) -> pd.DataFrame:
    """
    加载质谱数据，支持 .xlsx 和 .csv 格式。
    若未指定 file_path，则尝试常见文件名。

    ★ 加载后统一将 '##Time/min' 列名规范化为 'Time/min'，保证后续处理与输出一致。
    """
    if file_path is None:
        # 尝试常见文件名（按优先级）
        candidates = [
            DEFAULT_INPUT_FILE,                          # 首选：新目录结构
            INPUT_DIR / 'cleandata_ms.xlsx',
            SCRIPT_DIR / 'cleandata_ms.csv',
            SCRIPT_DIR / 'cleandata_ms.xlsx',
            SCRIPT_DIR / 'MS_data.csv',
            SCRIPT_DIR / 'MS_data.xlsx',
        ]
        for cand in candidates:
            if Path(cand).exists():
                file_path = str(cand)
                break
        else:
            raise FileNotFoundError(
                f"未找到数据文件。请先运行 input_ms.py，"
                f"确保 {DEFAULT_INPUT_FILE} 已生成。"
            )

    if file_path.endswith('.xlsx'):
        df = pd.read_excel(file_path, sheet_name='MS')
    elif file_path.endswith('.csv'):
        df = pd.read_csv(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {file_path}")

    # ★ 时间列名规范化
    df = _normalize_time_column(df)
    return df

def main():
    """主函数示例"""
    # 复位警告标志，确保每次运行都能看到完整警告
    MzBaselineNoiseDenoise.reset_warning()

    # ======================================================================
    # 0. 初始化输出目录与日志系统
    # ======================================================================
    dirs = setup_output_dirs()
    DATA_DIR_LOCAL    = dirs['data']
    FIGURES_DIR_LOCAL = dirs['figures']
    LOGS_DIR_LOCAL    = dirs['logs']
    DB_DIR_LOCAL      = dirs['Database_my']
    log_file = setup_logging(LOGS_DIR_LOCAL, log_name='denoise_ms')

    logging.info("=" * 70)
    logging.info("🚀 质谱 MS 清洗数据选择性去噪")
    logging.info("=" * 70)
    logging.info(f"📁 输入文件:     {DEFAULT_INPUT_FILE}")
    logging.info(f"📁 输出根目录:   {BASE_OUTPUT_DIR}")
    logging.info(f"📁 数据目录:     {DATA_DIR_LOCAL}")
    logging.info(f"📁 图片目录:     {FIGURES_DIR_LOCAL}")
    logging.info(f"📁 数据库目录:   {DB_DIR_LOCAL}")
    logging.info(f"📁 日志目录:     {LOGS_DIR_LOCAL}")
    logging.info(f"📝 本次日志:     {log_file}")
    logging.info("=" * 70)

    # 输入文件位置检查
    if not DEFAULT_INPUT_FILE.exists():
        logging.error(f"❌ 输入文件不存在：{DEFAULT_INPUT_FILE}")
        logging.error("请先运行 input_ms.py，确保 "
                      "cleandata_ms_results/data/cleandata_ms.csv 已生成。")
        return


    # ======================================================================
    # 示例1: 基本选择性去噪
    # ======================================================================
    def example_basic():
        print("示例1: 基本选择性去噪-CaCO3质谱检测分析")

        df = load_ms_data(str(DEFAULT_INPUT_FILE))

        config_manager = DenoiseConfigManager(default_method='savitzky_golay')

        # 先设置基础配置
        config_manager.set_column_config('mz44', 'wavelet',
                                        {'wavelet': 'db4', 'level': 3})
        config_manager.set_column_config('mz40', 'none')

        # 手动覆盖：将 mz44 改为 median 以演示配置优先级
        manual_config = {
            'mz44': {'method': 'median', 'params': {'window_size': 7}},
            'mz28': {'method': 'ewma', 'params': {'alpha': 0.4}},
        }
        config_manager.set_multiple_columns(manual_config)

        config_manager.set_columns_by_mz_range(
            df, (2, 26), 'savitzky_golay',
            {'window_size': 9, 'polyorder': 2}
        )

        config_manager.print_summary()

        denoiser = SelectiveDenoiser(config_manager)
        denoised_df, stats = denoiser.denoise(df, apply_to_all=False)
        denoiser.print_stats(stats)

        visualizer = DenoiseVisualizer()
        visualizer.plot_comparison(
            df, denoised_df, ['mz28', 'mz40', 'mz44'],
            time_range=(0, 1900)
        )

        visualizer.plot_row_comparison(
            df, denoised_df, max_plots=5, mz_range=(2, 64),
            save_path=str(FIGURES_DIR_LOCAL / 'row_comparison.png')
        )

        visualizer.plot_3d_comparison(
            df, denoised_df, stride=2, save_path=None, normalize_z=False
        )

        config_manager.save_to_file(
            str(DB_DIR_LOCAL / 'denoising_config_basic.json')
        )

        return denoised_df, config_manager, stats

    # ======================================================================
    # 示例2: 智能配置生成
    # ======================================================================
    def example_smart_config():
        print("\n示例2: 智能配置生成")

        df = load_ms_data(str(DEFAULT_INPUT_FILE))

        generator = SmartConfigGenerator(df)
        config_manager = generator.generate_by_snr()

        denoiser = SelectiveDenoiser(config_manager)
        denoised_df, stats = denoiser.denoise(df, apply_to_all=False)
        denoiser.print_stats(stats)

        visualizer = DenoiseVisualizer()
        visualizer.plot_comparison(
            df, denoised_df, ['mz28', 'mz40', 'mz44'],
            time_range=(0, 1900)
        )

        visualizer.plot_row_comparison(
            df, denoised_df, max_plots=4, mz_range=(2, 64),
            overlay=True, save_path=None
        )

        visualizer.plot_3d_comparison(
            df, denoised_df, stride=2, save_path=None, normalize_z=False
        )

        config_manager.save_to_file(
            str(DB_DIR_LOCAL / 'denoising_config_smart.json')
        )

        return denoised_df, config_manager, stats

    # ======================================================================
    # 示例3: 完整工作流程
    # ======================================================================
    def example_complete_workflow():
        print("\n示例3: 完整工作流程")

        try:
            df = load_ms_data(str(DEFAULT_INPUT_FILE))
            print(f"✓ 成功加载数据, 形状 {df.shape}")
        except FileNotFoundError:
            print(f"✗ 文件不存在，请检查路径")
            return None, None, None
        except Exception as e:
            print(f"✗ 读取文件出错: {e}")
            return None, None, None

        manual_config = {
            'mz28': {'method': 'wavelet',
                     'params': {'wavelet': 'db4', 'level': 3}},
            'mz44': {'method': 'wavelet',
                     'params': {'wavelet': 'db4', 'level': 3}},
            'mz40': {'method': 'kalman',
                     'params': {'process_variance': 1e-5,
                                'measurement_variance': 0.1}},
        }

        auto_mode = input(
            "自动生成配置模式 (snr/intensity/none) [默认snr]: "
        ).strip().lower()
        if auto_mode not in ['snr', 'intensity', 'none', '']:
            auto_mode = 'snr'
        auto_generate = None if auto_mode == 'none' else (auto_mode or 'snr')

        workflow = SelectiveDenoisingWorkflow(df)
        try:
            denoised_df, config_manager, stats = workflow.run(
                manual_config=manual_config,
                auto_generate=auto_generate
            )
            print("\n✓ 去噪流程执行成功")
        except Exception as e:
            print(f"✗ 工作流程执行失败: {e}")
            return None, None, None

        vis_choice = input(
            "是否显示去噪前后对比图？(y/n) [默认y]: "
        ).strip().lower()
        show_plots = vis_choice != 'n'

        save_plots_choice = input(
            "是否保存对比图为文件？(y/n) [默认n]: "
        ).strip().lower()
        save_path = None
        if save_plots_choice == 'y':
            default_save_path = str(FIGURES_DIR_LOCAL / 'comparison_plot.png')
            user_input = input(
                f"输入保存路径（直接回车使用默认: {default_save_path}）: "
            ).strip()
            save_path = user_input if user_input else default_save_path
            if save_path and not save_path.endswith(('.png', '.pdf', '.svg')):
                os.makedirs(save_path, exist_ok=True)
                save_path = os.path.join(save_path, 'comparison_plot.png')

        configured_cols = list(config_manager.column_configs.keys())
        if not configured_cols:
            configured_cols = df.columns[:3].tolist()
        else:
            configured_cols = configured_cols[:3]

        if show_plots or save_path:
            t_range_str = input(
                "请输入时间范围（如 0,500 表示前500个点，直接回车使用全部）: "
            ).strip()
            time_range = None
            if t_range_str:
                try:
                    start, end = map(int, t_range_str.split(','))
                    time_range = (start, end)
                except Exception:
                    print("格式错误，使用默认全范围")

            try:
                workflow.visualizer.plot_comparison(
                    df, denoised_df, configured_cols,
                    time_range=time_range,
                    save_path=save_path
                )
                if save_path:
                    print(f"✓ 对比图已保存至: {save_path}")
            except Exception as e:
                print(f"✗ 可视化出错: {e}")

            # 按行对比图 → FIGURES_DIR
            try:
                row_save_path = str(FIGURES_DIR_LOCAL / 'row_comparison.png')
                workflow.visualizer.plot_row_comparison(
                    df, denoised_df,
                    max_plots=5, mz_range=(2, 64),
                    save_path=row_save_path
                )
                print(f"✓ 行对比图已保存至: {row_save_path}")
            except Exception as e:
                print(f"✗ 行对比可视化出错: {e}")

        # 三维对比图
        vis_3d = input(
            "是否显示三维对比曲面图？(y/n) [默认n]: "
        ).strip().lower()
        if vis_3d == 'y':
            stride = 2
            save_3d = input(
                "是否保存三维图？(y/n) [默认n]: "
            ).strip().lower()
            save_path_3d = None
            if save_3d == 'y':
                default_3d_path = str(FIGURES_DIR_LOCAL / '3d_comparison.png')
                user_input = input(
                    f"输入保存路径（直接回车使用默认: {default_3d_path}）: "
                ).strip()
                save_path_3d = user_input if user_input else default_3d_path
                if save_path_3d and not save_path_3d.endswith(
                        ('.png', '.pdf', '.svg')):
                    save_path_3d = None
                    print("文件格式应为.png/.pdf/.svg，将不保存")
            try:
                workflow.visualizer.plot_3d_comparison(
                    df, denoised_df, stride=stride,
                    save_path=save_path_3d, normalize_z=False
                )
                if save_path_3d:
                    print(f"✓ 三维对比图已保存至: {save_path_3d}")
            except Exception as e:
                print(f"✗ 三维可视化出错: {e}")

        # 保存结果（数据 → DATA_DIR；配置/报告 → DB_DIR）
        print(f"\n默认数据目录: {DATA_DIR_LOCAL}")
        print(f"默认数据库目录: {DB_DIR_LOCAL}")
        output_dir = input(
            f"请输入数据保存目录 [默认 {DATA_DIR_LOCAL}]: "
        ).strip()
        if not output_dir:
            output_dir = str(DATA_DIR_LOCAL)

        saved_paths = {}
        try:
            saved_paths = workflow.save_results(
                denoised_df, stats=stats,
                output_dir=output_dir,
                db_dir=str(DB_DIR_LOCAL)          # ← 配置/报告固定入 Database_my
            )
            print("\n✓ 所有结果已保存:")
            for key, path in saved_paths.items():
                print(f"   {key}: {path}")
        except Exception as e:
            print(f"✗ 保存结果失败: {e}")

        # SNR 改善示例
        print("\n--- 信噪比改善示例 (SNR估算) ---")

        def estimate_snr(series):
            signal = series.mean()
            noise = series.std()
            return signal / noise if noise != 0 else np.inf

        sample_cols = configured_cols[:2]
        for col in sample_cols:
            if col in df.columns and col in denoised_df.columns:
                snr_before = estimate_snr(df[col])
                snr_after = estimate_snr(denoised_df[col])
                improvement = ((snr_after - snr_before) / snr_before * 100
                               if snr_before != 0 else 0)
                print(f"列 {col}: SNR {snr_before:.2f} → "
                      f"{snr_after:.2f} (改善 {improvement:.1f}%)")

        if 'report_path' in saved_paths and os.path.exists(saved_paths['report_path']):
            print("\n--- 报告摘要 (前10行) ---")
            with open(saved_paths['report_path'], 'r', encoding='utf-8') as f:
                lines = f.readlines()[:10]
                print(''.join(lines))
                if len(lines) == 10:
                    print("... (报告完整内容请查看文件)")

        return denoised_df, config_manager, stats

    # ======================================================================
    # 示例4: 同位素对处理
    # ======================================================================
    def example_isotope_pairs():
        print("\n示例4: 同位素对处理")

        df = load_ms_data(str(DEFAULT_INPUT_FILE))

        isotope_processor = IsotopePairProcessor()
        denoised_df, isotope_cols, ratio, stats = isotope_processor.process(
            df, base_mz=28.0, isotope_spacing=1.003355
        )

        visualizer = DenoiseVisualizer()
        visualizer.plot_comparison(df, denoised_df, isotope_cols, max_plots=2)
        visualizer.plot_3d_comparison(
            df, denoised_df, stride=2,
            save_path=None, normalize_z=False
        )

        return denoised_df, isotope_cols, ratio, stats

    # ======================================================================
    # 示例5: 批量处理
    # ======================================================================
    def example_batch_processing():
        print("\n示例5: 批量处理")

        file_list = [
            # 说明：此处以同一文件模拟批量输入（实际应用应传入不同样本的路径）
            str(DEFAULT_INPUT_FILE),
            str(DEFAULT_INPUT_FILE),
            str(DEFAULT_INPUT_FILE),
        ]

        config_template = {
            28.0: {'method': 'wavelet',
                   'params': {'wavelet': 'db4', 'level': 3}},
            44.0: {'method': 'savitzky_golay',
                   'params': {'window_size': 11}},
        }

        batch_processor = BatchDenoiseProcessor(config_template)
        results = batch_processor.process_files(
            file_list,
            output_dir=str(DATA_DIR_LOCAL / 'batch_results'),
            db_dir=str(DB_DIR_LOCAL),
            per_file_auto_config=True,
        )

        batch_processor.print_summary()

        if results:
            first_file = list(results.keys())[0]
            df_orig = (pd.read_csv(first_file)
                       if first_file.endswith('.csv')
                       else pd.read_excel(first_file))
            df_denoised = results[first_file]['denoised_df']
            visualizer = DenoiseVisualizer()
            visualizer.plot_3d_comparison(
                df_orig, df_denoised, stride=2,
                save_path=None, normalize_z=False
            )
        else:
            print("没有成功处理任何文件，无法进行可视化。")

        return results

    # ======================================================================
    # 示例6: mz5_11自研方法去噪
    def example_mz5_11():
        """示例6：使用自研 mz5_11 底谱噪声自适应方法"""
        print("\n示例6: mz5_11自研方法去噪")   # ← 放到函数内部

        df = load_ms_data(str(DEFAULT_INPUT_FILE))

        config_manager = DenoiseConfigManager(default_method='none')

        # 自动识别 m/z 5-11 范围内的列名
        no_gas_cols = []
        for col in df.columns:
            mz = DenoiseConfigManager._extract_mz_from_column(col)
            if mz is not None and 5.0 <= mz <= 11.0:
                no_gas_cols.append(col)

        print(f"自动识别无气体参考列: {no_gas_cols}")
        if not no_gas_cols:
            print("警告：未在数据中识别到 m/z 5-11 范围的列，方法将无法运行。")
            return None, None, None

        # 自动识别目标列（26-46 范围内的含气体列）
        target_cols = []
        for col in df.columns:
            mz = DenoiseConfigManager._extract_mz_from_column(col)
            if mz is not None and 26.0 <= mz <= 50.0:
                target_cols.append(col)
        if not target_cols:
            print("警告：未在数据中识别到 26-50 范围的含气体列，mz5_11 方法将无目标。")
            return None, None, None

        T_frac_str = input(
            f"标定时段占前多少比例？（默认 0.1，要求该时段内确无气体）: "
        ).strip()
        try:
            T_frac = float(T_frac_str) if T_frac_str else 0.1
        except ValueError:
            T_frac = 0.1
        T_frac = max(0.01, min(0.5, T_frac))    # 合理范围

        # 方案 A：只对指定几个列用自研方法
        config_manager.set_mz5_11_config(
            columns=target_cols,            # 想处理的含气体列
            no_gas_mz=no_gas_cols,          # 无气体参考列，自动识别
            T_idx=None,                     # 自动取标定时段（比例由 T_frac 决定）
            T_frac=T_frac,
            k=31,
            max_lag=10,
        )
        print(f"使用 T_frac={T_frac}（占前 {T_frac*100:.1f}% 时间点作为标定时段）")

        # 方案 B：对某个 m/z 范围的所有列统一用自研方法
        # config_manager.set_columns_by_mz_range(
        #     df, mz_range=(26, 50), method='mz5_11',
        #     params={
        #         'no_gas_mz': ['mz5','mz6','mz7','mz8','mz9','mz10','mz11'],
        #         'T_frac': 0.1, 'k': 5, 'max_lag': 30,
        #     }
        # )

        config_manager.print_summary()

        denoiser = SelectiveDenoiser(config_manager)
        denoised_df, stats = denoiser.denoise(df)
        denoiser.print_stats(stats)

        # 对比可视化
        vis = DenoiseVisualizer()

        # 参考底谱单独可视化
        fig, axes = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
        for col in no_gas_cols:
            axes[0].plot(df[col].values, alpha=0.5, linewidth=0.8, label=col)
        axes[0].set_title('Reference baseline channels (m/z 5-11) — Original')
        axes[0].set_ylabel('Intensity'); axes[0].legend(ncol=4, fontsize=8)
        axes[1].plot(df[no_gas_cols].mean(axis=1).values, color='black')
        axes[1].set_title('x_ref = mean(mz5..mz11)')
        axes[1].set_xlabel('Scan'); axes[1].set_ylabel('Intensity')
        fig.tight_layout()
        fig.savefig(str(FIGURES_DIR_LOCAL / 'mz5_11_baseline.png'), dpi=300)
        plt.close(fig)

        n_show = min(len(target_cols) + len(no_gas_cols), 12)   # 限制最多 12 行避免图太大
        vis.plot_comparison(df, denoised_df,
                            no_gas_cols + target_cols,
                            time_range=(0, 1900),
                            max_plots=n_show,
                            save_path=str(FIGURES_DIR_LOCAL / 'mz5_11_comparison.png'))

        vis.plot_3d_comparison(df, denoised_df, stride=2,
                            save_path=None, normalize_z=False)

        # 保存结果
        config_manager.save_to_file(str(DB_DIR_LOCAL / 'denoising_config_mz5_11.json'))

        return denoised_df, config_manager, stats

    # ======================================================================
    # 交互选择示例
    # ======================================================================
    print("选择示例:")
    print("1. 基本选择性去噪")
    print("2. 智能配置生成")
    print("3. 完整工作流程")
    print("4. 同位素对处理")
    print("5. 批量处理")
    print("6. mz5_11自研方法去噪")

    choice = input("请输入选择 (1-6): ")

    result = None
    if choice == '1':
        result = example_basic()
    elif choice == '2':
        result = example_smart_config()
    elif choice == '3':
        result = example_complete_workflow()
    elif choice == '4':
        result = example_isotope_pairs()
    elif choice == '5':
        result = example_batch_processing()
    elif choice == '6':
        result = example_mz5_11()
    else:
        print("无效选择")

    logging.info("=" * 70)
    logging.info(f"🎉 处理完毕！所有结果已保存至: {BASE_OUTPUT_DIR}")
    logging.info("=" * 70)

    return result

# %% ==================== 主程序入口 ====================
if __name__ == "__main__":
    main()