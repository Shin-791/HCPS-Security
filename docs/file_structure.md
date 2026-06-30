# File Structure and Evaluation Flow

## 1. このドキュメントの目的

このドキュメントは、HCPS-Securityリポジトリの主要ファイルと、ATI評価器の流れを説明するものです。

## 2. 主要ディレクトリ

| ディレクトリ | 役割 |
|---|---|
| `data/hcps_mock/` | HCPS/SOC-OT mock scenarioとサンプルtrajectory |
| `prompts/` | LLMエージェントへの指示 |
| `scripts/` | 変換器・評価器・Gemini実験スクリプト・半動的mock評価スクリプト |
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

### `scripts/simulate_user_response.py`

ルールベースのユーザー返答生成器です。

入力として、scenario、agentの初期authority mode、proposed actionを受け取り、operator役の返答を1つ生成します。

扱うユーザー状態の例:

- `normal`
- `overloaded`
- `confused`
- `urgency_pressure`
- `limited_information`

これはLLMユーザーシミュレーターではありません。学部卒で扱える範囲に収めるため、発話テンプレートを使った小さなシミュレーターにしています。

### `scripts/generate_second_turn_prompts.py`

保存済みのfirst-turn agent outputを読み込み、ルールベースuser simulatorの返答を付けて、2ターン目Gemini実験用のpromptをJSONLとして生成します。

重要な点:

- Gemini/Vertex AIは呼ばない
- API keyや認証情報は扱わない
- `agent_eval_inputs.json` のagent-visible contextだけをpromptに入れる
- evaluator-only labelsやscore capはpromptに入れない
- これはprompt生成であり、モデル評価結果ではない

生成物は `generated_prompts/` に出力できます。このディレクトリはローカル実験用で、誤commitを避けるためignore対象にしています。


### `scripts/run_second_turn_gemini_eval.py`

`generated_prompts/second_turn_prompts.jsonl` を読み、将来の2ターン目Gemini実験で送るprompt recordを処理するrunnerです。

重要な点:

- dry-runではGemini/Vertex AIを呼ばない
- dry-runではAPI key、ADC、credentials、tokenを読まない
- 実API実行には `--execute` が必須
- raw responseは `outputs/second_turn_gemini_raw/` に保存する
- 保存したraw responseは `normalize_second_turn_gemini_outputs.py` で正規化する

このスクリプトは、2ターン目実験を安全に実行するための入口です。実行結果そのものを卒研・論文に使う場合は、raw outputではなく、ATI trajectory、score report、安全なsummaryへ変換してから扱います。

### `scripts/run_rule_based_dynamic_eval.py`

2ターン程度の半動的mock評価を実行します。

流れ:

1. `agent_eval_inputs.json` を読む
2. mockの初期agent判断を作る
3. `simulate_user_response.py` でユーザー返答を作る
4. mockの最終agent判断を作る
5. `dynamic_sample_trajectories.json` にATI trajectoryとして保存する
6. 既存のATI評価器で採点する

実Gemini APIやVertex AIは呼びません。

### `data/hcps_mock/dynamic_sample_trajectories.json`

半動的評価用のサンプルtrajectoryです。

既存3シナリオに対して、初期agent判断、ユーザー返答、最終agent判断を `dynamic_interaction` に保存しています。ATI評価器が読む主要フィールドは既存trajectory形式に合わせているため、静的評価器を壊さずに評価できます。

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
- `outputs/second_turn_gemini_raw/`
- `outputs/second_turn_gemini_*.json`
- `outputs/second_turn_ati_*.json`

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

## 5. 半動的評価の流れ

半動的評価では、LLMやGeminiを呼ばず、mockの初期判断と最終判断を使います。

```text
agent_eval_inputs.json
        |
        v
mock initial agent decision
        |
        v
simulate_user_response.py
        |
        v
mock final agent decision
        |
        v
dynamic_sample_trajectories.json
        |
        v
evaluate_authority_transfer.py
```

この仕組みにより、静的な1回回答だけでなく、「ユーザーが焦る・誤解する・高負荷で短い説明を求める」状況に対して、agentが最終的に安全側へ切り替えられるかを小さく確認できます。

## 6. 2ターン目prompt生成の流れ

2ターン目prompt生成は、保存済みfirst-turn outputを使って、将来のGemini実験に渡すpromptだけを作る処理です。モデルAPIは呼びません。

```text
saved first-turn agent outputs
        |
        v
simulate_user_response.py
        |
        v
generate_second_turn_prompts.py
        |
        v
generated_prompts/second_turn_prompts.jsonl
```

このJSONLには、system prompt、2ターン目user prompt、ルールベースoperator response、first-turn summaryが入ります。これは実験準備用の中間成果物であり、Geminiが動的対話で安全に振る舞ったことを示す評価結果ではありません。

