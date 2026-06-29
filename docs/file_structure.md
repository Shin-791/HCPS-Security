# File Structure and Evaluation Flow

## 1. このドキュメントの目的

このドキュメントは、HCPS-Securityリポジトリの主要ファイルと、ATI評価器の流れを説明するものです。

## 2. 主要ディレクトリ

| ディレクトリ | 役割 |
|---|---|
| `data/hcps_mock/` | HCPS/SOC-OT mock scenarioとサンプルtrajectory |
| `prompts/` | LLMエージェントへの指示 |
| `scripts/` | 変換器・評価器・Gemini実験スクリプト |
| `outputs/` | 実験出力や評価結果。Gemini raw outputは基本commitしない |
| `results/` | 発表・卒研用に整理した安全な要約 |
| `docs/` | 研究仕様・評価仕様・ファイル説明 |

## 3. 主要ファイル

### `data/hcps_mock/tasks.json`

評価器用のシナリオ定義です。

含まれるもの:

- scenario description
- agent-visible context
- evaluator-only labels
- dangerous actions
- expected authority mode
- acceptable authority modes
- score caps
- partial credit weights

これは評価器が使う完全版のtask定義です。LLMにそのまま見せるファイルではありません。

### `data/hcps_mock/agent_eval_inputs.json`

LLMに見せる入力です。

重要な点:

- evaluator-only labelsを含めない
- expected authority modeを含めない
- score capsを含めない
- operator state labelを直接含めない

LLMには、観測ログ、物理制約、危険操作メタデータ、operatorの発話傾向など、agent-visibleな情報だけを渡します。

### `prompts/hcps_agent_prompt.md`

LLMエージェントへの指示です。

エージェントには、以下をJSON形式で出力させます。

- observations used
- evidence used
- physical constraint check
- physical impact assessment
- selected authority mode
- approval request / response
- executed action
- hold / delay decision
- handoff event

### `scripts/run_gemini_agent_eval.py`

Gemini/Vertex AIを使って、LLMエージェント出力を生成するスクリプトです。

主な機能:

- promptとscenario inputの読み込み
- Gemini API key modeまたはVertex AI ADC modeへの対応
- dry-run対応
- 出力JSONの保存
- ATI trajectoryへの変換
- ATI評価器の実行

注意:

- API keyや認証情報はコードに書かない
- `.env` はcommitしない
- 実験raw outputは基本commitしない

### `scripts/convert_agent_output_to_ati_trajectory.py`

LLM出力をATI評価器が読めるtrajectory形式へ変換します。

この段階では、LLM judgeは使いません。semi-structured JSONをルールベースに正規化します。

### `scripts/evaluate_authority_transfer.py`

ATI評価器本体です。

主な処理:

1. `tasks.json` を読む
2. trajectory JSONを読む
3. checkpointを判定する
4. violationを検出する
5. partial creditを計算する
6. score capとsoft penaltyを適用する
7. JSON reportとMarkdown summaryを出力する

### `outputs/`

評価器や実験スクリプトの出力先です。

ただし、Gemini raw outputや実験中間ファイルは基本的にcommitしません。

`.gitignore` では以下を除外しています。

- `outputs/gemini_*.json`
- `outputs/ati_score_report_gemini.json`
- `outputs/ati_score_summary_gemini.md`
- `outputs/archive_ssl_error_old/`

### `results/`

発表・卒研用に整理した安全な要約を置く場所です。

例:

- `results/gemini_3trial_summary.md`

このファイルには、raw outputや認証情報ではなく、分析結果だけを載せます。

## 4. ATI評価の流れ

基本的な流れは以下です。

```text
agent_eval_inputs.json
        |
        v
LLM Agent + hcps_agent_prompt.md
        |
        v
gemini_agent_outputs.json  (raw output, 基本commitしない)
        |
        v
convert_agent_output_to_ati_trajectory.py
        |
        v
gemini_ati_trajectories.json  (中間結果, 基本commitしない)
        |
        v
evaluate_authority_transfer.py
        |
        v
ATI score report / summary
        |
        v
results/gemini_3trial_summary.md  (安全な要約)
```

## 5. 学部卒での使い方

学部卒では、主に以下を見れば研究全体を説明できます。

- `README.md`
- `docs/ati_evaluation_spec.md`
- `docs/research_scope_bachelor_master.md`
- `docs/file_structure.md`
- `results/gemini_3trial_summary.md`

コードの詳細を説明する場合は、`scripts/evaluate_authority_transfer.py` を中心に見るとよいです。
