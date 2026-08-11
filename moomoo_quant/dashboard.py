import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from moomoo_quant import config
from moomoo_quant.multi_strategy.admission import load_admission
from moomoo_quant.run_manifest import resolve_current_run


st.set_page_config(page_title="JPY 多策略量化控制台", layout="wide")

PORTFOLIO_NAMES = {
    "Strategy": "JPY 多资产趋势 v1",
    "SPY JPY": "SPY 买入持有（日元）",
    "QQQ JPY": "QQQ 买入持有（日元）",
    "Static Equal Weight": "静态四资产等权",
    "JPY Multi-Asset Trend v1": "JPY 多资产趋势 v1",
    "SPY Buy & Hold JPY": "SPY 买入持有（日元）",
    "QQQ Buy & Hold JPY": "QQQ 买入持有（日元）",
}

RESULT_FILES = {
    "comparison": "trend_comparison_equity.csv",
    "performance": "trend_performance_comparison.csv",
    "allocations": "trend_allocations.csv",
    "current": "trend_current_signal.csv",
    "prices": "trend_asset_prices_jpy.csv",
    "crisis": "trend_crisis_monthly.csv",
    "rebalances": "trend_rebalance_history.csv",
    "yearly": "trend_yearly_comparison.csv",
    "monthly": "trend_monthly_returns.csv",
    "metadata": "trend_data_metadata.csv",
}


@st.cache_data(show_spinner=False)
def load_dashboard_data() -> dict[str, pd.DataFrame]:
    result_dir, manifest = resolve_current_run()
    missing = [name for name in RESULT_FILES.values() if not (result_dir / name).exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))
    data = {key: pd.read_csv(result_dir / name) for key, name in RESULT_FILES.items()}
    for key in ("comparison", "allocations", "prices", "rebalances", "monthly"):
        date_col = "signal_date" if key == "allocations" else "date"
        if key == "rebalances":
            date_col = "signal_date"
        data[key][date_col] = pd.to_datetime(data[key][date_col])
    data["result_dir"] = result_dir
    data["manifest"] = manifest or {}
    return data


@st.cache_data(show_spinner=False)
def load_multi_strategy_data() -> dict:
    keys = (
        "strategies", "lifecycle", "risk", "proposals", "migrations", "signals",
        "fills", "equity", "positions", "reconciliations", "runner_events", "broker_orders",
    )
    output = {key: pd.DataFrame() for key in keys}
    if not config.LEDGER_PATH.exists():
        return output
    connection = sqlite3.connect(f"file:{config.LEDGER_PATH.as_posix()}?mode=ro", uri=True)
    try:
        for key, table in (
            ("strategies", "strategy_accounts"),
            ("lifecycle", "lifecycle_events"),
            ("risk", "risk_decisions"),
            ("proposals", "proposed_orders"),
            ("migrations", "migration_events"),
            ("signals", "signals"),
            ("fills", "virtual_fills"),
            ("equity", "equity_snapshots"),
            ("positions", "strategy_positions"),
            ("reconciliations", "reconciliation_records"),
            ("runner_events", "shadow_run_events"),
            ("broker_orders", "broker_order_records"),
        ):
            try:
                output[key] = pd.read_sql_query(f"SELECT * FROM {table}", connection)
            except pd.errors.DatabaseError:
                output[key] = pd.DataFrame()
    finally:
        connection.close()
    return output


@st.cache_data(show_spinner=False)
def load_mean_reversion_data() -> dict:
    summary_path = config.RESULTS_DIR / "mean_reversion_jpy_research.json"
    if not summary_path.exists():
        return {}
    output = {"summary": json.loads(summary_path.read_text(encoding="utf-8"))}
    for key, name in (
        ("equity", "mean_reversion_jpy_equity.csv"),
        ("trades", "mean_reversion_jpy_trades.csv"),
        ("monthly", "mean_reversion_jpy_monthly.csv"),
        ("yearly", "mean_reversion_jpy_yearly.csv"),
    ):
        path = config.RESULTS_DIR / name
        output[key] = pd.read_csv(path) if path.exists() else pd.DataFrame()
    return output


@st.cache_data(show_spinner=False)
def load_research_data(prefix: str) -> dict:
    summary_path = config.RESULTS_DIR / f"{prefix}_research.json"
    if not summary_path.exists():
        return {}
    output = {"summary": json.loads(summary_path.read_text(encoding="utf-8"))}
    for key in ("equity", "trades", "monthly", "yearly", "stability", "signals"):
        path = config.RESULTS_DIR / f"{prefix}_{key}.csv"
        try:
            output[key] = pd.read_csv(path) if path.exists() else pd.DataFrame()
        except pd.errors.EmptyDataError:
            output[key] = pd.DataFrame()
    return output


@st.cache_data(show_spinner=False)
def load_operational_replay() -> dict:
    path = config.RESULTS_DIR / "operational_replay.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@st.cache_data(show_spinner=False)
def load_admissions() -> dict:
    return {
        "A": load_admission(config.TREND_STRATEGY_ID, "1") or {},
        "B": load_admission(config.DEFENSIVE_FACTOR_STRATEGY_ID, "2") or {},
        "C": load_admission(config.RISK_PARITY_STRATEGY_ID, "1") or {},
    }


@st.cache_data(show_spinner=False)
def load_shadow_data() -> dict:
    state_path = config.SHADOW_DIR / "state.json"
    if not state_path.exists():
        return {}
    with state_path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    output = {"state": state}
    for key in ("signals", "orders", "trades", "equity"):
        path = config.SHADOW_DIR / f"{key}.csv"
        output[key] = pd.read_csv(path) if path.exists() else pd.DataFrame()
    return output


def pct(value) -> str:
    return "—" if pd.isna(value) else f"{float(value):.2%}"


STATUS_NAMES = {
    "BASELINE_NOT_EXECUTED": "已记录初始基准信号，未追溯执行",
    "PENDING_REBALANCE": "等待下一交易日开盘理论记账",
    "NO_ACTION": "等待下一次月末信号",
    "WAITING_NEXT_MONTH_END": "等待下一次月末信号",
    "WAITING_NEXT_HALF_YEAR_END": "等待下一次半年末信号",
    "RESEARCH_REJECTED": "研究门槛未通过",
    "RESEARCH_INVALID": "研究证据无效",
    "SHADOW_READY": "已通过统一准入",
    "PROPOSED_ONLY": "拟议订单（不会发送）",
    "REJECTED": "已拒绝",
    "APPROVED_FOR_PROPOSAL": "仅批准生成拟议订单",
    "APPROVED_FOR_SHADOW": "允许本地影子记账",
    "WAITING_FOR_OPEN": "等待下一交易日开盘",
    "VIRTUAL_FILLED": "已完成本地假想成交",
    "RECONCILED": "已对账",
}


def date_text(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def allocation_text(raw: str) -> str:
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return str(raw)
    active = [f"{symbol.replace('US.', '')} {weight:.0%}" for symbol, weight in values.items() if float(weight) > 0]
    cash = max(1.0 - sum(float(weight) for weight in values.values()), 0.0)
    if cash > 0:
        active.append(f"日元现金 {cash:.0%}")
    return "、".join(active) or "日元现金 100%"


def normalized_figure(comparison: pd.DataFrame) -> go.Figure:
    names = ["Strategy", "SPY JPY", "QQQ JPY", "Static Equal Weight"]
    figure = go.Figure()
    for name in names:
        normalized = comparison[name] / comparison[name].iloc[0] * 100
        drawdown = comparison[f"{name} Drawdown"]
        figure.add_trace(
            go.Scatter(
                x=comparison["date"],
                y=normalized,
                name=PORTFOLIO_NAMES[name],
                mode="lines",
                customdata=np.column_stack([drawdown]),
                hovertemplate="日期：%{x|%Y-%m-%d}<br>净值：%{y:.2f}<br>回撤：%{customdata[0]:.2%}<extra>%{fullData.name}</extra>",
            )
        )
    figure.update_layout(hovermode="x unified", yaxis_title="初始值 100", legend_title_text="")
    return figure


def drawdown_figure(comparison: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    for name in ("Strategy", "SPY JPY", "QQQ JPY", "Static Equal Weight"):
        figure.add_trace(
            go.Scatter(
                x=comparison["date"],
                y=comparison[f"{name} Drawdown"],
                name=PORTFOLIO_NAMES[name],
                mode="lines",
                hovertemplate="日期：%{x|%Y-%m-%d}<br>回撤：%{y:.2%}<extra>%{fullData.name}</extra>",
            )
        )
    figure.update_layout(hovermode="x unified", yaxis_tickformat=".0%", legend_title_text="")
    return figure


def overview(data: dict[str, pd.DataFrame]) -> None:
    current = data["current"].iloc[-1]
    signal_date = pd.Timestamp(current["signal_date"])
    rebalances = data["rebalances"]
    executed = not rebalances[rebalances["signal_date"] == signal_date].empty

    st.header("机器人概览")
    st.caption("JPY Multi-Asset Trend v1，固定规则，仅用于理论信号与历史研究。")
    top = st.columns(3)
    top[0].metric("最新完整信号日期", signal_date.strftime("%Y-%m-%d"))
    top[1].metric("美元兑日元（USDJPY）", f"{float(current['usdjpy']):.3f}")
    top[2].caption("下一步")
    top[2].markdown(
        "**等待下一个月末，无需操作**" if executed else "**等待下一交易日开盘调仓**"
    )

    st.subheader("当前目标仓位")
    for label, column in (
        ("SPY", "SPY_weight"),
        ("QQQ", "QQQ_weight"),
        ("GLD", "GLD_weight"),
        ("IEF", "IEF_weight"),
        ("日元现金", "JPY_CASH_weight"),
    ):
        weight = float(current[column])
        st.progress(weight, text=f"{label}: {weight:.0%}")

    rows = []
    for symbol in ("SPY", "QQQ", "GLD", "IEF"):
        rank = current[f"{symbol}_rank"]
        rows.append(
            {
                "标的": symbol,
                "美元收盘价": current[f"{symbol}_usd_close"],
                "日元价格": current[f"{symbol}_jpy_price"],
                "12个月动量": current[f"{symbol}_momentum"] * 100,
                "10个月均线": current[f"{symbol}_sma"],
                "高于10个月均线": bool(current[f"{symbol}_above_sma"]),
                "符合持仓条件": bool(current[f"{symbol}_eligible"]),
                "动量排名": np.nan if pd.isna(rank) else int(float(rank)),
                "目标仓位": current[f"{symbol}_weight"] * 100,
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "美元收盘价": st.column_config.NumberColumn(format="$%.2f"),
            "日元价格": st.column_config.NumberColumn(format="¥%.2f"),
            "12个月动量": st.column_config.NumberColumn(format="%.2f%%"),
            "10个月均线": st.column_config.NumberColumn(format="¥%.2f"),
            "目标仓位": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )

    st.subheader("历史回测")
    performance = data["performance"].copy()
    display = performance.rename(
        columns={
            "portfolio": "组合",
            "total_return": "累计收益",
            "cagr": "年化收益率（CAGR）",
            "max_drawdown": "最大回撤",
            "sharpe": "夏普比率",
            "sortino": "索提诺比率",
            "volatility": "波动率",
            "calmar": "卡玛比率",
        }
    )
    display["组合"] = display["组合"].map(PORTFOLIO_NAMES).fillna(display["组合"])
    for column in ("累计收益", "年化收益率（CAGR）", "最大回撤", "波动率"):
        display[column] = display[column] * 100
    st.markdown("**我的策略**")
    st.dataframe(
        display[display["组合"] == "JPY 多资产趋势 v1"],
        hide_index=True,
        width="stretch",
        column_config={
            column: st.column_config.NumberColumn(format="%.2f%%")
            for column in ("累计收益", "年化收益率（CAGR）", "最大回撤", "波动率")
        },
    )
    st.markdown("**对照组**")
    st.caption("对照组只用于比较，不是机器人，不拥有预算、生命周期、仓位或订单。")
    st.dataframe(
        display[display["组合"] != "JPY 多资产趋势 v1"],
        hide_index=True,
        width="stretch",
        column_config={
            column: st.column_config.NumberColumn(format="%.2f%%")
            for column in ("累计收益", "年化收益率（CAGR）", "最大回撤", "波动率")
        },
    )

    st.subheader("我的策略与对照组净值")
    st.plotly_chart(normalized_figure(data["comparison"]), width="stretch")
    st.subheader("回撤曲线")
    st.plotly_chart(drawdown_figure(data["comparison"]), width="stretch")

    st.subheader("历史目标仓位")
    allocations = data["allocations"]
    figure = go.Figure()
    for label, column in (
        ("SPY", "SPY_weight"),
        ("QQQ", "QQQ_weight"),
        ("GLD", "GLD_weight"),
        ("IEF", "IEF_weight"),
        ("日元现金", "JPY_CASH_weight"),
    ):
        figure.add_trace(
            go.Scatter(
                x=allocations["signal_date"],
                y=allocations[column],
                name=label,
                stackgroup="one",
                hovertemplate="信号日期：%{x|%Y-%m-%d}<br>目标仓位：%{y:.0%}<extra>%{fullData.name}</extra>",
            )
        )
    figure.update_layout(yaxis_tickformat=".0%", yaxis_range=[0, 1], hovermode="x unified", legend_title_text="")
    figure.update_xaxes(rangeslider_visible=True)
    st.plotly_chart(figure, width="stretch")


def crisis_explorer(data: dict[str, pd.DataFrame]) -> None:
    st.title("危机回放")
    preset = st.selectbox(
        "回放区间",
        ("2008 金融危机", "2020 疫情冲击", "2022 股债双杀", "自定义区间"),
    )
    ranges = {
        "2008 金融危机": ("2007-01-01", "2009-12-31"),
        "2020 疫情冲击": ("2019-01-01", "2021-12-31"),
        "2022 股债双杀": ("2021-01-01", "2023-12-31"),
    }
    if preset == "自定义区间":
        selected = st.date_input(
            "日期范围",
            value=(data["prices"]["date"].min().date(), data["prices"]["date"].max().date()),
        )
        start, end = pd.Timestamp(selected[0]), pd.Timestamp(selected[-1])
    else:
        start, end = map(pd.Timestamp, ranges[preset])

    prices = data["prices"][(data["prices"]["date"] >= start) & (data["prices"]["date"] <= end)].copy()
    if prices.empty:
        st.warning("所选区间没有数据。")
        return

    st.subheader("资产日元价格")
    figure = go.Figure()
    for symbol in ("SPY", "QQQ", "GLD", "IEF"):
        values = prices[f"{symbol}_jpy_price"] / prices[f"{symbol}_jpy_price"].iloc[0] * 100
        figure.add_trace(go.Scatter(x=prices["date"], y=values, name=symbol, mode="lines"))
    figure.update_layout(yaxis_title="标准化日元价格（起点=100）", hovermode="x unified", legend_title_text="")
    st.plotly_chart(figure, width="stretch")

    st.subheader("策略净值")
    strategy = prices["Strategy_equity"] / prices["Strategy_equity"].iloc[0] * 100
    strategy_figure = go.Figure(go.Scatter(x=prices["date"], y=strategy, name="Strategy", mode="lines"))
    strategy_figure.update_layout(yaxis_title="标准化净值（起点=100）", hovermode="x unified")
    st.plotly_chart(strategy_figure, width="stretch")

    months = data["crisis"]
    month_start, month_end = start.strftime("%Y-%m"), end.strftime("%Y-%m")
    months = months[(months["month"] >= month_start) & (months["month"] <= month_end)].copy()
    st.subheader("逐月信号审计")
    st.caption("表中的目标仓位在当月月末生成，假想执行时间为下一交易日开盘。")
    crisis_names = {
        "month": "月份",
        "jpy_cash_weight": "日元现金目标仓位",
        "strategy_return": "策略月收益",
        "portfolio_equity": "组合净值",
        "usd_jpy": "美元兑日元",
        "signal_date": "信号日期",
    }
    for symbol in ("spy", "qqq", "gld", "ief"):
        ticker = symbol.upper()
        crisis_names.update(
            {
                f"{symbol}_momentum": f"{ticker} 12个月动量",
                f"{symbol}_jpy_price": f"{ticker} 日元价格",
                f"{symbol}_sma": f"{ticker} 10个月均线",
                f"{symbol}_above_sma": f"{ticker} 高于均线",
                f"{symbol}_eligible": f"{ticker} 符合持仓条件",
                f"{symbol}_rank": f"{ticker} 动量排名",
                f"{symbol}_weight": f"{ticker} 目标仓位",
                f"{symbol}_holding_weight": f"{ticker} 实际持仓权重",
                f"{symbol}_jpy_return": f"{ticker} 日元月收益",
                f"{symbol}_approx_contribution": f"{ticker} 近似收益贡献",
            }
        )
    st.dataframe(months.rename(columns=crisis_names), hide_index=True, width="stretch", height=520)

    if start <= pd.Timestamp("2022-12-31") and end >= pd.Timestamp("2022-01-01"):
        st.subheader("2022 年专项诊断")
        with (data["result_dir"] / "crisis_2022_summary.json").open(encoding="utf-8") as handle:
            summary = json.load(handle)
        st.write(f"2022 年策略收益：{summary['year_return']:.2%}")
        st.write("亏损最严重月份：", ", ".join(f"{x['month']} ({x['return']:.2%})" for x in summary["worst_months"]))
        st.write("SPY 失去持仓资格：", ", ".join(summary["spy_lost_eligible"]) or "没有发生")
        st.write("QQQ 失去持仓资格：", ", ".join(summary["qqq_lost_eligible"]) or "没有发生")
        st.write("实际持有 GLD 的月份：", ", ".join(summary["gld_held_months"]) or "无")
        st.write("实际持有 IEF 的月份：", ", ".join(summary["ief_held_months"]) or "无")
        st.info("2022 年回撤符合固定月频趋势规则和滞后信号；汇率对齐审计未发现未来数据填充。")


def backtest_details(data: dict[str, pd.DataFrame]) -> None:
    st.header("回测明细")
    st.subheader("历史调仓记录")
    rebalance_display = data["rebalances"].rename(
        columns={
            "signal_date": "信号日期",
            "execution_date": "假想执行日期",
            "previous_allocation": "原目标仓位",
            "new_allocation": "新目标仓位",
            "sold": "卖出",
            "bought": "买入",
            "turnover": "换手率",
            "commission_jpy": "手续费（日元）",
            "slippage_jpy": "滑点（日元）",
            "fx_cost_jpy": "换汇成本（日元）",
        }
    )
    rebalance_display = rebalance_display.sort_values("信号日期", ascending=False).copy()
    rebalance_display["信号日期"] = rebalance_display["信号日期"].map(date_text)
    rebalance_display["假想执行日期"] = rebalance_display["假想执行日期"].map(date_text)
    rebalance_display["原目标仓位"] = rebalance_display["原目标仓位"].map(allocation_text)
    rebalance_display["新目标仓位"] = rebalance_display["新目标仓位"].map(allocation_text)
    rebalance_display["换手率"] = rebalance_display["换手率"].map(pct)
    st.dataframe(rebalance_display, hide_index=True, width="stretch", height=480)

    st.subheader("年度收益")
    yearly = data["yearly"].copy()
    yearly = yearly.rename(columns={"year": "年份", **PORTFOLIO_NAMES})
    long = yearly.melt(id_vars="年份", var_name="组合", value_name="收益率")
    figure = px.bar(long, x="年份", y="收益率", color="组合", barmode="group")
    figure.update_layout(yaxis_tickformat=".0%", legend_title_text="")
    figure.add_hline(y=0, line_color="black", line_width=1)
    st.plotly_chart(figure, width="stretch")
    st.dataframe(yearly, hide_index=True, width="stretch")

    st.subheader("策略月度收益热力图")
    monthly = data["monthly"].pivot(index="year", columns="month", values="return")
    heatmap = go.Figure(
        go.Heatmap(
            z=monthly.values,
            x=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            y=monthly.index,
            colorscale="RdYlGn",
            zmid=0,
            colorbar_tickformat=".0%",
            hovertemplate="年份：%{y}<br>月份：%{x}<br>收益率：%{z:.2%}<extra></extra>",
        )
    )
    heatmap.update_layout(yaxis_autorange="reversed")
    st.plotly_chart(heatmap, width="stretch")

    st.subheader("数据检查")
    metadata = data["metadata"].rename(
        columns={
            "series": "数据序列",
            "source": "数据来源",
            "start_date": "起始日期",
            "end_date": "结束日期",
            "observations": "观测数",
            "last_updated": "最后更新",
            "alignment": "对齐说明",
        }
    )
    st.dataframe(metadata, hide_index=True, width="stretch")
    st.caption("信号收盘价使用当日或此前已知的最近汇率；开盘假想成交严格使用成交日前的最近汇率收盘价；不使用未来填充。")


def shadow_account() -> None:
    st.header("前向影子账户（旧版只读记录）")
    st.warning("这是本地理论记账，没有发送任何证券订单，也不连接任何交易账户。")
    shadow = load_shadow_data()
    if not shadow:
        st.info("影子账户尚未初始化。运行一次 daily 命令后会以 100,000 日元建立前向记录。")
        st.code("python -m moomoo_quant.main daily")
        return

    state = shadow["state"]
    equity = shadow["equity"]
    latest_equity = equity.iloc[-1] if not equity.empty else None
    initial = float(state["initial_cash_jpy"])
    current = float(state["current_equity_jpy"])
    total_return = current / initial - 1
    drawdown = current / float(state["peak_equity_jpy"]) - 1
    created = pd.Timestamp(state["created_at"])
    if created.tzinfo is None:
        created = created.tz_localize("UTC")
    running_days = max((pd.Timestamp.now(tz="UTC") - created).days, 0)
    has_baseline = not shadow["signals"].empty and "BASELINE_NOT_EXECUTED" in shadow["signals"]["status"].astype(str).values
    if has_baseline and shadow["trades"].empty:
        status_text = "已记录初始基准信号，但未追溯执行；等待下一次月末信号。"
    else:
        status_text = STATUS_NAMES.get(state["status"], state["status"])

    row_one = st.columns(3)
    row_one[0].metric("启动日期", created.tz_convert("Asia/Tokyo").strftime("%Y-%m-%d"))
    row_one[1].metric("初始资金", f"¥{initial:,.0f}")
    row_one[2].metric("当前净值", f"¥{current:,.0f}")
    row_two = st.columns(3)
    row_two[0].metric("累计收益", f"{total_return:.2%}")
    row_two[1].metric("当前回撤", f"{drawdown:.2%}")
    row_two[2].metric("运行天数", f"{running_days} 天")
    st.info(f"当前状态：{status_text}")

    st.subheader("实际影子仓位与目标仓位")
    rows = []
    for symbol in ("SPY", "QQQ", "GLD", "IEF"):
        rows.append(
            {
                "资产": symbol,
                "理论持有数量": float(state["positions"].get(symbol, 0.0)),
                "整股可执行估算": math.floor(float(state["positions"].get(symbol, 0.0))),
                "当前实际权重": (float(latest_equity.get(f"{symbol}_weight", 0.0)) * 100) if latest_equity is not None else 0.0,
                "当前目标权重": float(state["target_weights"].get(symbol, 0.0)) * 100,
            }
        )
    rows.append(
        {
            "资产": "日元现金",
            "理论持有数量": float(state["cash_jpy"]),
            "当前实际权重": (float(latest_equity.get("JPY_CASH_weight", 1.0)) * 100) if latest_equity is not None else 100.0,
            "当前目标权重": (1.0 - sum(float(x) for x in state["target_weights"].values())) * 100,
        }
    )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "当前实际权重": st.column_config.NumberColumn(format="%.2f%%"),
            "当前目标权重": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    st.subheader("最近信号")
    signals = shadow["signals"].tail(5).rename(
        columns={
            "signal_date": "信号日期",
            "selected": "入选资产",
            "target_weights": "目标仓位",
            "status": "状态",
            "recorded_at": "记录时间",
        }
    )
    if not signals.empty:
        signals["信号日期"] = signals["信号日期"].map(date_text)
        signals["记录时间"] = signals["记录时间"].map(date_text)
        signals["目标仓位"] = signals["目标仓位"].map(allocation_text)
        signals["状态"] = signals["状态"].map(lambda value: STATUS_NAMES.get(value, value))
    st.dataframe(signals, hide_index=True, width="stretch")

    st.subheader("最近假想成交")
    trades = shadow["trades"].tail(10).rename(
        columns={
            "execution_date": "假想执行日期",
            "asset": "资产",
            "side": "方向",
            "quantity": "数量",
            "execution_price_usd": "美元成交价",
            "notional_jpy": "名义金额（日元）",
            "commission_jpy": "手续费（日元）",
            "slippage_jpy": "滑点（日元）",
        }
    )
    if trades.empty:
        st.caption("尚无假想成交。")
    else:
        st.dataframe(trades, hide_index=True, width="stretch")

    st.subheader("影子账户净值曲线")
    if not equity.empty:
        figure = go.Figure(
            go.Scatter(
                x=pd.to_datetime(equity["date"]),
                y=equity["equity_jpy"],
                name="影子账户净值",
                mode="lines+markers",
                hovertemplate="日期：%{x|%Y-%m-%d}<br>净值：¥%{y:,.0f}<extra></extra>",
            )
        )
        figure.update_layout(yaxis_title="账户净值（日元）")
        st.plotly_chart(figure, width="stretch")


def _account_row(multi: dict, strategy_id: str, version: str) -> dict:
    frame = multi["strategies"]
    if frame.empty:
        return {}
    selected = frame[(frame["strategy_id"] == strategy_id) & (frame["strategy_version"].astype(str) == version)]
    return selected.iloc[-1].to_dict() if not selected.empty else {}


def _runtime_state(multi: dict, strategy_id: str, version: str) -> dict:
    account = _account_row(multi, strategy_id, version)
    if not account:
        return {
            "account": {},
            "budget_jpy": 0.0,
            "stage": "未注册",
            "status": "Shadow Ledger 中没有运行账户",
            "is_funded": False,
        }
    budget = float(account.get("allocated_capital_jpy", 0.0))
    raw_status = account.get("status", "RESEARCH")
    return {
        "account": account,
        "budget_jpy": budget,
        "stage": "影子（SHADOW）" if budget > 0 else "研究（RESEARCH）",
        "status": STATUS_NAMES.get(raw_status, raw_status),
        "is_funded": budget > 0,
    }


def _target_text(summary: dict, symbols: tuple[str, ...]) -> str:
    latest = summary.get("latest_target", {})
    parts = [f"{symbol} {float(latest.get(f'{symbol}_weight', 0)):.0%}" for symbol in symbols]
    cash = float(latest.get("JPY_CASH_weight", 0))
    if cash > 0.001:
        parts.append(f"日元现金 {cash:.0%}")
    return "、".join(parts)


def portfolio_overview(
    data: dict,
    multi: dict,
    defensive_factor: dict,
    risk_parity: dict,
) -> None:
    st.title("运行总览")
    st.warning("当前为 Forward Shadow。Kill switch 开启；不连接交易账户，不发送 SIMULATE 或 REAL 订单。")
    accounts = multi["strategies"]
    funded = accounts[accounts["allocated_capital_jpy"] > 0] if not accounts.empty else pd.DataFrame()
    allocated = float(funded["allocated_capital_jpy"].sum()) if not funded.empty else 0.0
    total_equity = float(funded["cash_jpy"].sum()) if not funded.empty else 0.0
    if not multi["equity"].empty and not funded.empty:
        funded_ids = set(funded["account_id"])
        latest_equity = multi["equity"][multi["equity"]["account_id"].isin(funded_ids)]
        latest_equity = latest_equity.sort_values("market_date").groupby("account_id").tail(1)
        total_equity = float(latest_equity["equity_jpy"].sum())
    if accounts.empty:
        st.info("尚未检测到 Shadow Ledger。以下绩效来自历史研究，不代表已有运行账户或持仓。")
    metrics = st.columns(4)
    metrics[0].metric("分配资金", f"¥{allocated:,.0f}")
    metrics[1].metric("影子组合净值", f"¥{total_equity:,.0f}")
    metrics[2].metric("前向影子收益", f"{total_equity / allocated - 1:.2%}" if allocated else "—")
    metrics[3].metric("运行机器人", f"{len(funded) if not funded.empty else 0} / 3")
    status_cols = st.columns(2)
    status_cols[0].metric("Kill switch", "开启")
    status_cols[1].metric("数据更新时间", date_text(data["manifest"].get("market_data_last_date")))

    portfolio = operational_replay_data.get("portfolio", {})
    portfolio_stats = portfolio.get("stats", {})
    if portfolio_stats:
        st.subheader("三机器人历史组合 · 成本后净回放")
        history = st.columns(5)
        history[0].metric("共同区间净 CAGR", pct(portfolio_stats.get("cagr")))
        history[1].metric("最大回撤", pct(portfolio_stats.get("max_drawdown")))
        history[2].metric("夏普比率", f"{portfolio_stats.get('sharpe', 0):.3f}")
        history[3].metric("共同区间", f"{portfolio.get('common_period_start')} 起")
        history[4].metric("组合总成本", f"¥{portfolio_stats.get('total_cost_jpy', 0):,.0f}")
        st.caption("三只机器人各自持有 ¥100,000 虚拟预算；佣金、滑点、自动换汇成本、0.001 股取整与现金拖累均已计入。")

    st.subheader("策略机器人")
    left, middle, right = st.columns(3)
    trend_stats = operational_replay_data.get("strategies", {}).get("A", {}).get("stats", data["performance"].iloc[0])
    current = data["current"].iloc[-1]
    with left.container(border=True):
        runtime = _runtime_state(multi, config.TREND_STRATEGY_ID, "1")
        st.markdown("### Robot A · JPY 多资产趋势 v1")
        st.write(f"阶段：**{runtime['stage']}**")
        st.write(f"分配资金：**¥{runtime['budget_jpy']:,.0f}**")
        st.write(f"成本后净 CAGR：**{float(trend_stats['cagr']):.2%}**")
        st.write(f"历史最大回撤：**{float(trend_stats['max_drawdown']):.2%}**")
        st.write("历史最新目标：**SPY 50%、QQQ 50%**")
        st.write(f"最近信号：**{date_text(current['signal_date'])}**")
        st.write(f"状态：**{runtime['status']}**")
    with middle.container(border=True):
        summary = defensive_factor.get("summary", {})
        stats = operational_replay_data.get("strategies", {}).get("B", {}).get("stats", summary.get("stats", {}))
        runtime = _runtime_state(multi, config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
        st.markdown("### Robot B · 美股质量低波动 v2")
        st.write(f"阶段：**{runtime['stage']}**")
        st.write(f"分配资金：**¥{runtime['budget_jpy']:,.0f}**")
        st.write(f"成本后净 CAGR：**{stats.get('cagr', 0):.2%}**")
        st.write(f"历史最大回撤：**{stats.get('max_drawdown', 0):.2%}**")
        st.write(f"历史最新目标：**{_target_text(summary, ('QUAL', 'USMV'))}**")
        st.write(f"状态：**{runtime['status']}**")
    with right.container(border=True):
        summary = risk_parity.get("summary", {})
        stats = operational_replay_data.get("strategies", {}).get("C", {}).get("stats", summary.get("stats", {}))
        runtime = _runtime_state(multi, config.RISK_PARITY_STRATEGY_ID, "1")
        st.markdown("### Robot C · JPY 无杠杆风险平价 v1")
        st.write(f"阶段：**{runtime['stage']}**")
        st.write(f"分配资金：**¥{runtime['budget_jpy']:,.0f}**")
        st.write(f"成本后净 CAGR：**{stats.get('cagr', 0):.2%}**")
        st.write(f"历史最大回撤：**{stats.get('max_drawdown', 0):.2%}**")
        st.write(f"历史最新目标：**{_target_text(summary, ('SPY', 'GLD', 'IEF'))}**")
        st.write(f"状态：**{runtime['status']}**")

    st.caption("失败策略已移入历史档案；Benchmark 只在机器人详情中作为对照，不拥有预算、仓位或订单。")


def merged_holdings(data: dict, multi: dict, defensive_factor: dict, risk_parity: dict) -> None:
    st.title("合并理论持仓")
    st.warning("Portfolio Manager 已按机器人预算合并目标；当前只展示理论数量，不发送订单。")
    current = data["current"].iloc[-1]
    factor_latest = defensive_factor.get("summary", {}).get("latest_target", {})
    parity_latest = risk_parity.get("summary", {}).get("latest_target", {})
    budgets = {
        "Robot A · 趋势 v1": float(_account_row(multi, config.TREND_STRATEGY_ID, "1").get("allocated_capital_jpy", 0)),
        "Robot B · 质量低波动 v2": float(_account_row(multi, config.DEFENSIVE_FACTOR_STRATEGY_ID, "2").get("allocated_capital_jpy", 0)),
        "Robot C · 风险平价 v1": float(_account_row(multi, config.RISK_PARITY_STRATEGY_ID, "1").get("allocated_capital_jpy", 0)),
    }
    targets = {
        "Robot A · 趋势 v1": {symbol: float(current[f"{symbol}_weight"]) for symbol in ("SPY", "QQQ", "GLD", "IEF")},
        "Robot B · 质量低波动 v2": {symbol: float(factor_latest.get(f"{symbol}_weight", 0)) for symbol in ("QUAL", "USMV")},
        "Robot C · 风险平价 v1": {symbol: float(parity_latest.get(f"{symbol}_weight", 0)) for symbol in ("SPY", "GLD", "IEF")},
    }
    prices = {symbol: float(current[f"{symbol}_jpy_price"]) for symbol in ("SPY", "QQQ", "GLD", "IEF")}
    prices.update({symbol: float(factor_latest.get(f"{symbol}_jpy_price", 0)) for symbol in ("QUAL", "USMV")})
    dates = {symbol: date_text(current["signal_date"]) for symbol in ("SPY", "QQQ", "GLD", "IEF")}
    dates.update({symbol: date_text(factor_latest.get("signal_date")) for symbol in ("QUAL", "USMV")})
    positions = multi.get("positions", pd.DataFrame())
    current_quantities = positions.groupby("symbol")["quantity"].sum().to_dict() if not positions.empty else {}
    symbols = ("SPY", "QQQ", "GLD", "IEF", "QUAL", "USMV")
    contributions: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    rows = []
    total_budget = sum(budgets.values())
    for symbol in symbols:
        price_jpy = prices.get(symbol, 0.0)
        notional = 0.0
        for robot, robot_targets in targets.items():
            weight = robot_targets.get(symbol, 0.0)
            if weight <= 0 or budgets[robot] <= 0:
                continue
            robot_notional = budgets[robot] * weight
            notional += robot_notional
            contributions[symbol].append(
                {
                    "机器人": robot,
                    "理论碎股数量": robot_notional / price_jpy if price_jpy else 0.0,
                    "目标名义金额（日元）": robot_notional,
                    "策略内目标权重": weight * 100,
                }
            )
        target_quantity = notional / price_jpy if price_jpy else 0.0
        current_quantity = float(current_quantities.get(symbol, 0.0))
        rows.append(
            {
                "标的": symbol,
                "聚合目标碎股数量": target_quantity,
                "整股可执行估算": math.floor(target_quantity),
                "聚合目标金额（日元）": notional,
                "当前影子数量": current_quantity,
                "当前影子市值（日元）": current_quantity * price_jpy,
                "组合目标权重": notional / total_budget * 100 if total_budget else 0.0,
                "理论差额数量": target_quantity - current_quantity,
                "贡献机器人": "、".join(item["机器人"] for item in contributions[symbol]) or "无",
                "目标数据日期": dates[symbol],
                "订单状态": "未生成（Kill switch 开启）",
            }
        )
    holdings = pd.DataFrame(rows)
    st.dataframe(
        holdings,
        hide_index=True,
        width="stretch",
        column_config={
            "聚合目标碎股数量": st.column_config.NumberColumn(format="%.6f"),
            "聚合目标金额（日元）": st.column_config.NumberColumn(format="¥%.0f"),
            "当前影子市值（日元）": st.column_config.NumberColumn(format="¥%.0f"),
            "组合目标权重": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )
    st.download_button("下载合并目标 CSV", holdings.to_csv(index=False).encode("utf-8-sig"), "aggregated_targets.csv", "text/csv")
    st.subheader("策略贡献明细")
    for symbol in symbols:
        if not contributions[symbol]:
            continue
        with st.expander(f"{symbol} 的策略归属"):
            st.dataframe(
                pd.DataFrame(contributions[symbol]), hide_index=True, width="stretch",
                column_config={"策略内目标权重": st.column_config.NumberColumn(format="%.0f%%")},
            )


def mean_reversion_detail(data: dict, mr: dict) -> None:
    st.header("US ETF 短期均值回归 v1")
    if not mr:
        st.info("尚未运行固定规格研究。")
        return
    summary = mr["summary"]
    stats = summary["stats"]
    st.error("研究结论：成本后正期望门槛未通过，保持 RESEARCH，不进入 SHADOW。")
    cols = st.columns(4)
    cols[0].metric("总收益（JPY）", f"{stats['net_return']:.2%}")
    cols[1].metric("年化收益率", f"{stats['cagr']:.2%}")
    cols[2].metric("最大回撤", f"{stats['max_drawdown']:.2%}")
    cols[3].metric("夏普比率", f"{stats['sharpe']:.2f}")
    st.subheader("固定规格与成本诊断")
    diagnostics = pd.DataFrame(
        [
            ("毛收益（Gross Return）", f"{stats['gross_return']:.2%}"),
            ("净收益（Net Return）", f"{stats['net_return']:.2%}"),
            ("手续费", f"¥{stats['fees_jpy']:,.0f}"),
            ("滑点", f"¥{stats['slippage_jpy']:,.0f}"),
            ("换汇成本", f"¥{stats['fx_cost_jpy']:,.0f}"),
            ("平均单笔期望（Expectancy）", f"{stats['net_expectancy']:.3%}"),
            ("交易次数", f"{stats['trade_count']} 笔"),
            ("与 Trend v1 月收益相关性", f"{summary['monthly_correlation_with_trend_v1']:.3f}"),
        ], columns=["指标", "结果"]
    )
    st.dataframe(diagnostics, hide_index=True, width="stretch")
    gates = pd.DataFrame(
        [{"预注册门槛": name, "结果": "通过" if passed else "未通过"} for name, passed in summary["acceptance_gates"].items()]
    )
    st.dataframe(gates, hide_index=True, width="stretch")

    equity = mr["equity"].copy()
    equity["date"] = pd.to_datetime(equity["date"])
    figure = go.Figure(go.Scatter(x=equity["date"], y=equity["equity_jpy"], name="我的策略", mode="lines"))
    figure.update_layout(yaxis_title="净值（日元）", hovermode="x unified")
    st.plotly_chart(figure, width="stretch")
    st.subheader("对照组")
    st.caption("SPY 买入持有（日元）仅用于比较，不是机器人。")
    benchmark = data["comparison"][["date", "SPY JPY"]].copy()
    benchmark["标准化净值"] = benchmark["SPY JPY"] / benchmark["SPY JPY"].iloc[0] * config.MR_INITIAL_CASH_JPY
    benchmark_figure = go.Figure(go.Scatter(x=benchmark["date"], y=benchmark["标准化净值"], name="对照组：SPY 买入持有（日元）"))
    st.plotly_chart(benchmark_figure, width="stretch")


def research_detail(
    title: str,
    research: dict,
    correlation_labels: tuple[str, ...],
    admission: dict | None = None,
    operational: dict | None = None,
) -> None:
    st.header(title)
    if not research:
        st.info("尚未运行冻结规格的首次正式研究。")
        return
    summary = research["summary"]
    admission = admission or {}
    stats = dict(summary["stats"])
    stats.update((operational or {}).get("stats", {}))
    if admission.get("decision") == "SHADOW_READY":
        st.success("统一硬门槛已通过；已进入 Forward Shadow，历史信号不追溯成交。")
        if "does_not_improve_spy_jpy_max_drawdown" in admission.get("warnings", []):
            st.warning("对照警告：最大回撤没有优于同期 SPY（日元）。这是组合评估项，不再单独否决策略。")
    elif summary["status"] == "SHADOW":
        st.success("固定规格验收全部通过；已进入 Forward Shadow，历史信号不追溯成交。")
    else:
        st.error("固定规格验收未通过；已归档，预算为 ¥0。")
    cols = st.columns(4)
    cols[0].metric("净收益（日元）", pct(stats["net_return"]))
    cols[1].metric("年化收益率", pct(stats["cagr"]))
    cols[2].metric("最大回撤", pct(stats["max_drawdown"]))
    cols[3].metric("夏普比率", f"{stats['sharpe']:.3f}")
    details = [
        ("收益口径", "NET（已扣佣金、滑点、换汇成本及碎股取整现金拖累）"),
        ("交易成本合计", f"¥{stats['total_cost_jpy']:,.0f}"),
        ("换手率", f"{stats['turnover']:.2f}x"),
        ("交易次数", f"{stats['trade_count']}"),
        ("平均持有/调仓间隔", f"{stats['average_holding_days']:.2f} 天"),
        ("Sortino", f"{stats['sortino']:.3f}"),
        ("Calmar", f"{stats['calmar']:.3f}"),
        ("年化波动率", pct(stats["volatility"])),
        ("最差月份", f"{stats['worst_month_label']} · {pct(stats['worst_month'])}"),
        ("最长回撤期", f"{stats['drawdown_duration_sessions']} 个交易日"),
    ]
    for key in correlation_labels:
        if summary.get(key) is not None:
            label = "与 Robot A 相关性" if "trend" in key else "与其他候选机器人相关性"
            details.append((label, f"{summary[key]:.3f}"))
    st.dataframe(pd.DataFrame(details, columns=["指标", "首次正式结果"]), hide_index=True, width="stretch")
    if admission:
        st.subheader("统一策略准入 v1")
        st.dataframe(
            pd.DataFrame([{"硬门槛": key, "结果": "通过" if value else "未通过"} for key, value in admission["hard_checks"].items()]),
            hide_index=True,
            width="stretch",
        )
        st.caption("Benchmark、相关性和个别市场阶段表现作为警告或组合配置依据，不再被当作单策略致命门槛。")
    else:
        st.subheader("固定验收结果")
        st.dataframe(
            pd.DataFrame([{"预注册门槛": key, "结果": "通过" if value else "未通过"} for key, value in summary["acceptance_gates"].items()]),
            hide_index=True,
            width="stretch",
        )
    equity = research["equity"].copy()
    equity["date"] = pd.to_datetime(equity["date"])
    figure = go.Figure(go.Scatter(x=equity["date"], y=equity["equity_jpy"], name="净值（日元）", mode="lines"))
    figure.update_layout(yaxis_title="净值（日元）", hovermode="x unified")
    st.plotly_chart(figure, width="stretch")
    if not research["stability"].empty:
        st.subheader("稳定性诊断")
        st.caption("只用于识别脆弱性，不参与选择参数，也不会覆盖首次正式结果。")
        st.dataframe(research["stability"], hide_index=True, width="stretch")


def robot_details(
    data: dict,
    multi: dict,
    mr: dict,
    stress: dict,
    defensive_factor_v1: dict,
    defensive_factor_v2: dict,
    risk_parity: dict,
) -> None:
    st.title("机器人详情")
    robot = st.selectbox(
        "选择机器人",
        (
            "Robot A · JPY 多资产趋势 v1",
            "Robot B · 美股质量低波动 v2",
            "Robot C · JPY 无杠杆风险平价 v1",
        ),
    )
    if robot.startswith("Robot B"):
        tabs = st.tabs(("运行状态", "历史回测", "前向影子", "历史档案"))
        with tabs[0]:
            summary = defensive_factor_v2.get("summary", {})
            runtime = _runtime_state(multi, config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
            st.subheader("Robot B · 美股质量低波动 v2")
            cols = st.columns(4)
            cols[0].metric("运行阶段", runtime["stage"])
            cols[1].metric("分配资金", f"¥{runtime['budget_jpy']:,.0f}")
            cols[2].metric("历史最新目标", _target_text(summary, ("QUAL", "USMV")))
            cols[3].metric("下次检查", "6 月或 12 月月末" if runtime["is_funded"] else "无（研究拒绝）")
            st.write(f"运行状态：**{runtime['status']}**")
            st.write(f"统一准入：**{admission_data['B'].get('decision', '尚无准入记录')}**")
            st.caption("质量 + 低波动因子，固定 50% / 50%，半年调仓；不使用趋势、动量或均值回归。")
        with tabs[1]:
            research_detail(
                "US Quality & Low Volatility v2",
                defensive_factor_v2,
                ("monthly_correlation_with_trend_v1", "monthly_correlation_with_risk_parity_v1"),
                admission_data["B"],
                operational_replay_data.get("strategies", {}).get("B", {}),
            )
        with tabs[2]:
            if runtime["is_funded"]:
                st.info("2026-06-30 只作为初始基线，不追溯成交；等待下一次真实半年末信号。")
            else:
                st.info("当前研究门槛未通过，没有运行预算、影子持仓或待执行信号。")
        with tabs[3]:
            archived = []
            for name, item in (
                ("防御多因子 v1", defensive_factor_v1),
                ("压力回撤均值回归 v2", stress),
                ("短期均值回归 v1", mr),
            ):
                summary = item.get("summary", {}) if item else {}
                stats = summary.get("stats", {})
                archived.append(
                    {
                        "策略": name,
                        "状态": summary.get("status", "RESEARCH_REJECTED"),
                        "年化收益率": stats.get("cagr"),
                        "最大回撤": stats.get("max_drawdown"),
                        "预算": "¥0",
                        "原因": "、".join(summary.get("rejection_reasons", [])) or "历史成本后门槛未通过",
                    }
                )
            st.dataframe(pd.DataFrame(archived), hide_index=True, width="stretch")
        return
    if robot.startswith("Robot C"):
        tabs = st.tabs(("历史回测", "目标风险分配", "前向影子"))
        with tabs[0]:
            research_detail(
                "JPY Unlevered Risk Parity v1",
                risk_parity,
                ("monthly_correlation_with_trend_v1", "monthly_correlation_with_defensive_factor_v2"),
                admission_data["C"],
                operational_replay_data.get("strategies", {}).get("C", {}),
            )
        with tabs[1]:
            latest = risk_parity.get("summary", {}).get("latest_target", {})
            rows = []
            for symbol in ("SPY", "GLD", "IEF"):
                rows.append({"资产": symbol, "目标权重": float(latest.get(f"{symbol}_weight", 0)), "风险贡献": float(latest.get(f"{symbol}_risk_contribution", 0))})
            rows.append({"资产": "日元现金", "目标权重": float(latest.get("JPY_CASH_weight", 0)), "风险贡献": 0.0})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={"目标权重": st.column_config.ProgressColumn(format="%.1%%", min_value=0, max_value=1), "风险贡献": st.column_config.NumberColumn(format="%.1%%")})
        with tabs[2]:
            st.info("影子账户已建立，但 2026-07-31 基准信号不会追溯成交；等待下一次真实月末。")
        return
    tabs = st.tabs(("机器人概览", "回测明细", "危机回放", "前向影子", "运行清单"))
    with tabs[0]:
        overview(data)
    with tabs[1]:
        backtest_details(data)
    with tabs[2]:
        crisis_explorer(data)
    with tabs[3]:
        shadow_account()
    with tabs[4]:
        st.subheader("统一回测运行清单")
        manifest = data["manifest"]
        st.write(f"Run ID：`{manifest.get('run_id', '旧结果无 run_id')}`")
        manifest_rows = pd.DataFrame(
            [(key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value) for key, value in manifest.items()],
            columns=["字段", "值"],
        )
        st.dataframe(manifest_rows, hide_index=True, width="stretch")


def robot_comparison(data: dict, defensive_factor: dict, risk_parity: dict, multi: dict) -> None:
    st.title("运行策略比较")
    trend = operational_replay_data.get("strategies", {}).get("A", {}).get("stats", data["performance"].iloc[0])
    factor_summary = defensive_factor.get("summary", {})
    parity_summary = risk_parity.get("summary", {})
    factor_stats = operational_replay_data.get("strategies", {}).get("B", {}).get("stats", factor_summary.get("stats", {}))
    parity_stats = operational_replay_data.get("strategies", {}).get("C", {}).get("stats", parity_summary.get("stats", {}))
    trend_runtime = _runtime_state(multi, config.TREND_STRATEGY_ID, "1")
    factor_runtime = _runtime_state(multi, config.DEFENSIVE_FACTOR_STRATEGY_ID, "2")
    parity_runtime = _runtime_state(multi, config.RISK_PARITY_STRATEGY_ID, "1")
    rows = [
        {"策略": "Robot A · 趋势", "版本": "1", "生命周期": trend_runtime["stage"], "预算": f"¥{trend_runtime['budget_jpy']:,.0f}", "净 CAGR（全成本）": pct(trend["cagr"]), "最大回撤": pct(trend["max_drawdown"]), "夏普比率": f"{trend['sharpe']:.3f}", "净收益": pct(trend["net_return"]), "总成本（日元）": f"¥{trend.get('total_cost_jpy', 0):,.0f}", "当前仓位": "SPY 50%、QQQ 50%", "下一检查": "下一个美股月末", "准入警告": "执行回放不覆盖冻结基线"},
        {"策略": "Robot B · 质量低波动", "版本": "2", "生命周期": factor_runtime["stage"], "预算": f"¥{factor_runtime['budget_jpy']:,.0f}", "净 CAGR（全成本）": pct(factor_stats.get("cagr")), "最大回撤": pct(factor_stats.get("max_drawdown")), "夏普比率": f"{factor_stats.get('sharpe', 0):.3f}", "净收益": pct(factor_stats.get("net_return")), "总成本（日元）": f"¥{factor_stats.get('total_cost_jpy', 0):,.0f}", "当前仓位": _target_text(factor_summary, ("QUAL", "USMV")), "下一检查": "下一个半年末", "准入警告": "最大回撤未优于同期 SPY（日元）"},
        {"策略": "Robot C · 风险平价", "版本": "1", "生命周期": parity_runtime["stage"], "预算": f"¥{parity_runtime['budget_jpy']:,.0f}", "净 CAGR（全成本）": pct(parity_stats.get("cagr")), "最大回撤": pct(parity_stats.get("max_drawdown")), "夏普比率": f"{parity_stats.get('sharpe', 0):.3f}", "净收益": pct(parity_stats.get("net_return")), "总成本（日元）": f"¥{parity_stats.get('total_cost_jpy', 0):,.0f}", "当前仓位": _target_text(parity_summary, ("SPY", "GLD", "IEF")), "下一检查": "下一个美股月末", "准入警告": ""},
    ]
    comparison = pd.DataFrame(rows)
    st.dataframe(
        comparison,
        hide_index=True,
        width="stretch",
    )
    st.subheader("月收益相关性矩阵")
    path = config.RESULTS_DIR / "strategy_correlation_matrix.csv"
    if path.exists():
        matrix = pd.read_csv(path, index_col=0)
        figure = px.imshow(matrix, text_auto=".3f", zmin=-1, zmax=1, color_continuous_scale="RdBu_r", aspect="auto")
        figure.update_layout(coloraxis_colorbar_title="相关性")
        st.plotly_chart(figure, width="stretch")


def safety_records(multi: dict) -> None:
    st.title("运行与安全")
    st.error("拟议订单（仅理论计算，不会发送）")
    st.write("Moomoo SIMULATE：已实现，等待用户单独批准启用。当前固定配置为关闭，页面没有启用入口。")
    st.caption("Execution Scope 始终禁用；不读取交易账户，不解锁交易，不发送任何订单。")
    st.subheader("风险决策")
    if multi["risk"].empty:
        st.info("尚无风险决策记录。")
    else:
        risk = multi["risk"].sort_values("checked_at", ascending=False).copy()
        risk["status"] = risk["status"].map(lambda value: STATUS_NAMES.get(value, value))
        st.dataframe(risk, hide_index=True, width="stretch")
    st.subheader("拟议订单")
    if multi["proposals"].empty:
        st.info("当前没有拟议订单。Kill switch 开启，初始基准信号也不会追溯生成。")
    else:
        proposals = multi["proposals"].sort_values("created_at", ascending=False).copy()
        proposals["status"] = proposals["status"].map(lambda value: STATUS_NAMES.get(value, value))
        st.dataframe(proposals, hide_index=True, width="stretch")
    st.subheader("生命周期")
    st.dataframe(multi["lifecycle"], hide_index=True, width="stretch")
    st.caption("不存在自动生命周期升级。SIMULATE 适配器默认禁用；本项目没有 REAL 适配器。")
    st.subheader("Forward Shadow Runner")
    if multi["runner_events"].empty:
        st.info("尚无前向事件。Robot A / C 等待月末；Robot B 等待半年末。")
    else:
        events = multi["runner_events"].drop(columns=["details_json"], errors="ignore")
        events["state"] = events["state"].map(lambda value: STATUS_NAMES.get(value, value))
        st.dataframe(events.sort_values("created_at", ascending=False), hide_index=True, width="stretch")
    st.write(f"Virtual Fill：**{len(multi['fills'])}**；Broker Order 记录：**{len(multi['broker_orders'])}**")
    st.subheader("数据迁移")
    st.dataframe(multi["migrations"], hide_index=True, width="stretch")


st.sidebar.title("JPY 量化控制台")
page = st.sidebar.radio("页面", ("运行总览", "机器人详情", "运行策略比较", "合并理论持仓", "运行与安全"))
if config.QUANT_ADMIN_MODE:
    if st.sidebar.button("重新运行回测", type="secondary", width="stretch"):
        with st.spinner("正在更新市场数据并重建历史结果……"):
            from moomoo_quant.backtest.trend_analysis import run_full_trend_analysis

            run_full_trend_analysis()
            load_dashboard_data.clear()
            st.rerun()
    st.sidebar.caption("管理员模式已开启。")
else:
    st.sidebar.caption("生产只读模式")
st.sidebar.caption("不显示账户资料，不连接交易环境，不发送订单。")

try:
    dashboard_data = load_dashboard_data()
except FileNotFoundError as exc:
    st.error(f"缺少 Dashboard 结果文件：{exc}")
    st.code("python -m moomoo_quant.main trend-backtest")
    st.stop()

multi_strategy_data = load_multi_strategy_data()
mean_reversion_data = load_mean_reversion_data()
stress_pullback_data = load_research_data("stress_pullback_v2")
defensive_factor_v1_data = load_research_data("defensive_factor_v1")
defensive_factor_v2_data = load_research_data("defensive_factor_v2")
risk_parity_data = load_research_data("risk_parity_v1")
operational_replay_data = load_operational_replay()
admission_data = load_admissions()

if page == "运行总览":
    portfolio_overview(dashboard_data, multi_strategy_data, defensive_factor_v2_data, risk_parity_data)
elif page == "机器人详情":
    robot_details(
        dashboard_data,
        multi_strategy_data,
        mean_reversion_data,
        stress_pullback_data,
        defensive_factor_v1_data,
        defensive_factor_v2_data,
        risk_parity_data,
    )
elif page == "运行策略比较":
    robot_comparison(dashboard_data, defensive_factor_v2_data, risk_parity_data, multi_strategy_data)
elif page == "合并理论持仓":
    merged_holdings(dashboard_data, multi_strategy_data, defensive_factor_v2_data, risk_parity_data)
else:
    safety_records(multi_strategy_data)
