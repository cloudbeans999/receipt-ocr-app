import streamlit as st
import os
import base64
import json
import requests
import pandas as pd
import datetime
import re

# ページ設定
st.set_page_config(page_title="レシートOCRアプリ", layout="wide")

st.title("🧾 レシートAI読み取り & JSON変換")

# APIキー入力（サイドバー）
api_key = st.sidebar.text_input("Perplexity API Key", type="password")
if not api_key:
    # st.secretsから読み込む場合の処理もここに書けます
    st.warning("サイドバーでAPIキーを入力してください。")
    st.stop()

# ファイルアップロード
uploaded_files = st.file_uploader(
    "レシートPDFをアップロード (最大5ファイル)", 
    type=["pdf"], 
    accept_multiple_files=True
)

# 勘定科目ファイルのアップロード (オプション)
kanjokamoku_file = st.sidebar.file_uploader("勘定科目リスト (txt)", type=["txt"])

# 指示プロンプト (デフォルト値を設定)
default_prompt = """
必ず以下のキーを持つJSONオブジェクトの配列（リスト）だけを出力してください。その他のものを返答する必要は一切ありません。Markdownのコードブロック（``````）は不要です。

 １．勘定科目(kanjokamoku.txt)一覧がユーザーから渡されます。勘定科目がユーザーから渡されない場合は、勘定科目の欄はすべて「その他」とすること。

２．レシート画像を次の#{json形式}でOCRしてスプレッドシートで出力してください。
#{json形式}

[
  {
    "file_name": "ファイル名",
    "page_number": "PDFのページ番号",
    "date": "YYYY/MM/DD",
    "category": "勘定科目",
    "amount": "金額",
    "tax_rate": "消費税率",
    "invoice_flag": "0 or5 or 52",
    "merchant": "取引先名",
    "description": "品目・内容",
    "invoce_number": "13桁の数字"

  }
]

３．OCRを行う条件
(A)勘定科目はユーザーから受け取った勘定科目一覧から選択すること。勘定科目が判別できない場合は「その他」を選ぶこと

(B)消費税とインボイスの処理ルール：
(1) 消費税が含まれていない場合
「消費税率」→ 0
「インボイス有無」→（空白）

(2) 消費税が含まれている場合
インボイス番号（Tから始まる13桁の数字）がある → 「インボイス有無」は「5」
インボイス番号がない → 「インボイス有無」は「52」

(3) 複数の税率が含まれるレシートの場合
消費税率ごとに行を分けて出力する
各行に税込み金額を記載する

(4) 解読不明な箇所は、「不明」と記載すること。PDFにおいて、ページ全体が読み込めない場合にも、すべてのファイルとそのPDFに含まれるページを読み込んだことを確かめるために、無視せずにjsonのデータに含めること。

出力例：
[
  {
    "file_name": "picture1.jpg",
    "page_number": 1,
    "date": "2025/11/01",
    "category": "会議費",
    "amount": "1300",
    "tax_rate": "8",
    "invoice_flag": "52",
    "merchant": "キヤヌルシェ大阪",
    "description": "コーヒー",
    "invoce_number": "1234567891012"
  },
  {
    "file_name": "picture2.jpg",
    "page_number": 1,
    "date": "2025/11/02",
    "category": "発送配達費",
    "amount": "3750",
    "tax_rate": "10",
    "invoice_flag": "5",
    "merchant": "日本郵便株式会社",
    "description": "切手",
    "invoce_number": "6123457891012"
  },
  {
    "file_name": "document1.pdf",
    "page_number": 1,
    "date": "2025/11/10",
    "category": "仕入",
    "amount": "108",
    "tax_rate": "8",
    "invoice_flag": "5",
    "merchant": "●●スーパー",
    "description": "食品",
    "invoce_number": "1234567101289"
  },
  {
    "file_name": "document1.pdf",
    "page_number": 1,
    "date": "2025/11/10",
    "category": "消耗品費",
    "amount": "220",
    "tax_rate": "10",
    "invoice_flag": "5",
    "merchant": "●●スーパー",
    "description": "雑貨",
    "invoce_number": "1234567101289"
  },
  {
    "file_name": "document1.pdf",
    "page_number": 2,
    "date": "2025/10/31",
    "category": "租税公課",
    "amount": "400",
    "tax_rate": "0",
    "invoice_flag": "",
    "merchant": "郵便局",
    "description": "印紙",
    "invoce_number": "2345678910123"
  },
  {
    "file_name": "document1.pdf",
    "page_number": 3,
    "date": "不明",
    "category": "その他",
    "amount": "不明",
    "tax_rate": "0",
    "invoice_flag": "",
    "merchant": "不明",
    "description": "不明",
    "invoce_number": ""
  }

]

注意：document1.pdfの1ページ目は、8%の商品と10%の商品が含まれているレシート。document1.pdfの2ページ目は、郵便局で購入した印紙であり消費税は対象外のため（空白となる）。document1.pdfの3ページ目は、印刷が不鮮明で読み取れないレシート。

必ずJSONオブジェクトの配列（リスト）だけを出力すること。
"""
direction_prompt = st.sidebar.text_area("AIへの指示", value=default_prompt, height=150)

# # --- 関数定義 (既存ロジックを流用) ---
def extract_json_data(file_bytes, file_name, prompt, api_key, kanjo_bytes):
    endpoint = "https://api.perplexity.ai/chat/completions"
    pdf_b64 = base64.b64encode(file_bytes).decode("utf-8")
    
    system_prompt = f"""{prompt}
    処理対象ファイル名: {file_name}
    【重要】出力はマークダウン記法を含まず、必ず純粋なJSON配列のテキストのみを返してください。"""

    content = [
        {"type": "text", "text": system_prompt},
        {"type": "file_url", "file_url": {"url": pdf_b64}, "file_name": file_name},
    ]

    if kanjo_bytes:
        kanjo_b64 = base64.b64encode(kanjo_bytes).decode("utf-8")
        content.append({"type": "file_url", "file_url": {"url": kanjo_b64}, "file_name": "kanjokamoku.txt"})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": content}],
        "stream": False
    }

    try:
        response = requests.post(endpoint, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        content_text = response.json()["choices"][0]["message"]["content"]
        
        # --- 変更箇所: 正規表現によるJSON抽出ロジックの強化 ---
        
        # 1. re.DOTALLを使って改行を含む文字列全体から検索
        # 2. r'\[.*\]' は「最初の [」から「最後の ]」までを貪欲にマッチさせます
        # これにより、前後の挨拶文やMarkdown記法（```json 等）を無視してリスト部分だけを取り出せます
        json_match = re.search(r'\[.*\]', content_text, re.DOTALL)
        
        if json_match:
            clean_json = json_match.group(0)
        else:
            # マッチしない場合は、仕方ないので元のテキスト全体を試す
            clean_json = content_text

        # JSON変換
        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError as e:
            # JSONエラー時にデバッグしやすいよう詳細を表示
            st.error(f"JSON変換エラー: {file_name}\nAIからの返答を解析できませんでした。")
            with st.expander("AIの生返答を確認"):
                st.code(content_text)
            return []

        return data if isinstance(data, list) else [data]
        # --- 変更箇所ここまで ---

    except Exception as e:
        st.error(f"{file_name} の処理中にエラー: {e}")
        return []


# def extract_json_data(file_bytes, file_name, prompt, api_key, kanjo_bytes):
#     endpoint = "https://api.perplexity.ai/chat/completions"
#     pdf_b64 = base64.b64encode(file_bytes).decode("utf-8")
    
#     system_prompt = f"""{prompt}
#     処理対象ファイル名: {file_name}
#     【重要】出力はマークダウン記法を含まず、必ず純粋なJSON配列のテキストのみを返してください。"""

#     content = [
#         {"type": "text", "text": system_prompt},
#         {"type": "file_url", "file_url": {"url": pdf_b64}, "file_name": file_name},
#     ]

#     if kanjo_bytes:
#         kanjo_b64 = base64.b64encode(kanjo_bytes).decode("utf-8")
#         content.append({"type": "file_url", "file_url": {"url": kanjo_b64}, "file_name": "kanjokamoku.txt"})

#     headers = {
#         "Authorization": f"Bearer {api_key}",
#         "Content-Type": "application/json",
#     }
#     payload = {
#         "model": "sonar-pro",
#         "messages": [{"role": "user", "content": content}],
#         "stream": False
#     }

#     try:
#         response = requests.post(endpoint, headers=headers, data=json.dumps(payload))
#         response.raise_for_status()
#         content_text = response.json()["choices"][0]["message"]["content"]
        
#         # JSON抽出ロジック
#         json_match = re.search(r"""``````""", content_text, re.DOTALL)
#         clean_json = json_match.group(1) if json_match else content_text.strip().strip('`')
        
#         data = json.loads(clean_json)
#         return data if isinstance(data, list) else [data]
#     except Exception as e:
#         st.error(f"{file_name} の処理中にエラー: {e}")
#         return []

# --- 実行ボタン ---
if st.button("読み取り開始"):
    if not uploaded_files:
        st.error("ファイルをアップロードしてください。")
    elif len(uploaded_files) > 5:
        st.error("ファイル数は5つ以内にしてください。")
    else:
        # 勘定科目の読み込み
        kanjo_bytes = kanjokamoku_file.read() if kanjokamoku_file else None
        
        all_data = []
        progress_bar = st.progress(0)

        for i, uploaded_file in enumerate(uploaded_files):
            st.info(f"処理中: {uploaded_file.name} ...")
            # アップロードされたファイルのバイトデータを直接渡す
            file_bytes = uploaded_file.read()
            data = extract_json_data(file_bytes, uploaded_file.name, direction_prompt, api_key, kanjo_bytes)
            if data:
                all_data.extend(data)
            progress_bar.progress((i + 1) / len(uploaded_files))

        if all_data:
            st.success("完了しました！")
            
            # 結果の表示
            st.json(all_data)
            
            # JSONダウンロードボタン
            json_str = json.dumps(all_data, ensure_ascii=False, indent=2)
            st.download_button(
                label="JSONファイルをダウンロード",
                data=json_str,
                file_name=f"receipt_data_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json"
            )
