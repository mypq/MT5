GOLD BOT — MASTER PROJECT PLAN

1. Project Identity

Project Name: GOLD BOT

Platform: Android + MetaTrader 5

Target Market: Gold (XAUUSD)

Application Language: Arabic (RTL)

Server: Private Windows VPS

Trading Mode: DEMO ONLY

Primary Goal: Build a professional gold market analysis and optional automated trading system.

2. Core Operating Modes

Mode A: Intelligent Manual Analysis

The user requests an analysis at any time.

The system must:

- Collect current market data.
- Analyze multiple timeframes.
- Evaluate technical indicators.
- Analyze price action and market structure.
- Analyze liquidity and supply/demand zones where data supports it.
- Analyze economic news and relevant events.
- Evaluate volatility and trading costs.
- Identify supporting and opposing evidence.
- Produce a concise Arabic report.

This mode must never open trades.

Mode B: Optional Automated Trading

The user can explicitly enable or disable automated trading.

The system must:

- Use the same validated analysis engine.
- Verify all entry conditions.
- Calculate position size using risk limits.
- Set protective Stop Loss and Take Profit.
- Reject unsafe or invalid trades.
- Record every trading decision.
- Stop opening new positions when safety conditions fail.

Manual analysis and automated trading must have independent controls.

3. Analysis Architecture

The analysis engine will contain these specialist modules:

1. Trend Analysis
2. Price Action Analysis
3. Technical Indicators
4. Market Structure Analysis
5. Liquidity Analysis
6. Supply and Demand Analysis
7. Volume and Order Flow Analysis
8. Economic Analysis
9. News Analysis
10. Intermarket Analysis
11. Quantitative Analysis
12. Risk Management

Each module must contain real analytical logic, validated inputs, outputs and tests.

Unsupported or unavailable market data must be identified rather than fabricated.

4. Decision Engine

The decision engine combines specialist outputs and evaluates:

- Evidence quality.
- Agreement and disagreement.
- Market conditions.
- Data freshness.
- Risk and execution costs.

Allowed final decisions:

- BUY
- SELL
- WAIT
- NO TRADE

Every decision must include an explanation and supporting evidence.

Confidence scores must not be presented as guaranteed win probabilities.

5. Android Application

The Android application must:

- Use Arabic throughout.
- Support RTL layout.
- Provide secure authentication.
- Show account and connection status.
- Request manual analysis.
- Display concise analysis reports.
- Enable and disable automated Demo trading.
- Show open positions and trade history.
- Display profit, loss and risk information.
- Provide emergency controls.
- Show alerts and system errors.

All controls must be connected to working backend functions.

6. Backend

The backend is responsible for:

- Authentication and authorization.
- Secure device registration.
- Analysis orchestration.
- Market data integration.
- News and economic data integration.
- Command validation.
- Command expiration and unique identifiers.
- Trading status.
- Trade history.
- Audit logs.
- Risk policy enforcement.
- Health monitoring.

All external communication must use HTTPS.

The backend must never accept arbitrary executable code from the Android application.

7. MetaTrader 5 Integration

The MQL5 Expert Advisor is responsible for:

- Connecting to the approved backend.
- Reporting market and account status.
- Receiving authorized commands.
- Executing approved Demo trades.
- Applying protective trading rules.
- Reporting positions and execution results.
- Handling connection failures safely.

The Expert Advisor must not accept arbitrary trading commands or bypass risk controls.

8. Mandatory Safety Rules

- Demo-only operation is mandatory.
- Real-money trading must be disabled in this project version.
- No Martingale.
- No unlimited position sizing.
- No unlimited simultaneous positions.
- No opening trades with stale or invalid data.
- No opening trades when critical risk checks fail.
- Existing protective Stop Loss and Take Profit must not be removed on connection loss.
- Restart must not automatically resume trading without state verification.
- Emergency stop must block new entries.

9. Testing and Quality Requirements

Every implemented module must be tested.

Testing must include:

- Unit tests.
- Integration tests.
- Historical strategy testing.
- Out-of-sample testing.
- Demo forward testing.
- Network failure testing.
- Risk limit testing.
- Android application testing.
- MT5 execution verification.

A feature is not complete merely because its source file exists.

10. Development Order

Phase 1: Project architecture and specifications.

Phase 2: Backend foundation and security.

Phase 3: Market data and analysis modules.

Phase 4: Decision engine and risk management.

Phase 5: MQL5 integration.

Phase 6: Android application.

Phase 7: Integration and Demo testing.

Phase 8: Performance evaluation and documentation.

11. Completion Policy

Never mark a feature complete without:

1. Implemented source code.
2. Real integration with required components.
3. Successful relevant tests.
4. Error handling.
5. Documentation.

No empty modules, fake functionality, placeholder implementations or fabricated performance claims.

12. Project Principle

GOLD BOT must be a reliable, explainable and testable gold analysis and Demo trading system.

The user should receive the essential trading information in a concise Arabic interface while the system performs the complex analysis internally.
