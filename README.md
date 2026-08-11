# moomoo_quant

以 JPY 为基准的个人多策略量化运行项目，使用 moomoo OpenAPI 获取公开市场数据，包含冻结回测、前向影子账户、组合聚合、风险检查和只读 Streamlit 运行控制台。

当前运行机器人为 JPY Trend v1、US Quality & Low Volatility v2 和 JPY Unlevered Risk Parity v1。项目不发送模拟或真实订单；交易执行层带有强制安全保护，默认启用 kill switch，并拒绝任何真实交易环境。

完整安装、运行、安全边界和部署说明见 [项目文档](moomoo_quant/README.md)。
