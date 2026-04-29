import os
import json
import base64
from openai import AzureOpenAI

# 优先读取环境变量，方便 Docker/CI 部署时覆盖
API_KEY    = os.environ.get("AZURE_OPENAI_API_KEY",    "")
ENDPOINT   = os.environ.get("AZURE_OPENAI_ENDPOINT",   "")
MODEL_NAME = os.environ.get("AZURE_OPENAI_MODEL",      "gemini-2.5-pro")

client = AzureOpenAI(
    api_key=API_KEY,
    azure_endpoint=ENDPOINT,
    api_version="2024-03-01-preview",
)


def encode_video_to_base64(video_path):
    with open(video_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def load_prompt():
    prompt_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config", "prompt_analyze_video.txt"
    )
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def analyze_video_quality_and_features(video_path):
    """
    使用大模型分析视频：提取 18 个维度特征，判断质量。
    返回解析后的 dict，失败返回 None。
    """
    print(f"[*] 正在分析视频: {os.path.basename(video_path)}")

    try:
        base64_video = encode_video_to_base64(video_path)
    except Exception as e:
        print(f"    [-] 视频读取失败: {e}")
        return None

    prompt_text = load_prompt()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:video/mp4;base64,{base64_video}"},
                },
            ],
        }
    ]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            stream=False,
            max_tokens=4096,
            temperature=0.3,
            extra_headers={"X-TT-LOGID": "auto_analyze_pipeline"},
            response_format={"type": "json_object"},
            extra_body={
                "thinking": {
                    "include_thoughts": True,
                    "budget_tokens": 1024,
                }
            },
        )

        result_text = response.choices[0].message.content.strip()
        # 清洗可能残留的 Markdown 代码块标记
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].strip()

        parsed = json.loads(result_text)
        print(f"    [✓] 分析完成，质量: {'优质' if parsed.get('is_high_quality') else '低质'}")
        return parsed

    except Exception as e:
        print(f"    [-] 模型解析失败: {e}")
        return None
