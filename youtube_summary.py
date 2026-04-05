import os
import json
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# 모니터링할 채널 목록 (이름, 채널ID)
CHANNELS = [
    ("필승", "UClYlwu2zmmL-prY8U0TkUQg"),
]

LAST_VIDEO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_videos.json")


def load_last_videos():
    try:
        with open(LAST_VIDEO_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_last_videos(data):
    with open(LAST_VIDEO_FILE, "w") as f:
        json.dump(data, f)


def get_latest_videos(channel_id):
    """YouTube RSS 피드에서 최신 영상 목록 가져오기"""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    resp = requests.get(url, timeout=10)
    root = ET.fromstring(resp.content)
    ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}

    videos = []
    for entry in root.findall("atom:entry", ns):
        video_id = entry.find("atom:id", ns).text.split(":")[-1]
        title = entry.find("atom:title", ns).text
        published = entry.find("atom:published", ns).text
        link = entry.find("atom:link", ns).get("href")
        videos.append({
            "id": video_id,
            "title": title,
            "published": published,
            "link": link,
        })
    return videos


def get_transcript(video_id):
    """YouTube 자막 가져오기"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        resp = requests.get(url, timeout=10)
        html = resp.text

        pattern = r'"captionTracks":\[.*?"baseUrl":"(.*?)"'
        match = re.search(pattern, html)
        if not match:
            return None

        caption_url = match.group(1).replace("\\u0026", "&")
        caption_resp = requests.get(caption_url, timeout=10)
        caption_xml = ET.fromstring(caption_resp.content)

        texts = []
        for text_elem in caption_xml.findall(".//text"):
            if text_elem.text:
                texts.append(text_elem.text.replace("&#39;", "'").replace("&amp;", "&"))

        return " ".join(texts) if texts else None
    except Exception:
        return None


def summarize_with_openai(title, transcript):
    """GPT-4o-mini로 영상 내용 요약"""
    if transcript:
        prompt = f"""다음은 유튜브 영상의 자막입니다. 핵심 내용을 한국어로 3-5개 bullet point로 요약해주세요.

영상 제목: {title}

자막 내용:
{transcript[:8000]}"""
    else:
        prompt = f"""다음 유튜브 영상 제목을 보고, 이 영상이 어떤 내용일지 간단히 설명해주세요.

영상 제목: {title}

(자막을 가져올 수 없어 제목 기반으로 설명합니다)"""

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        },
        timeout=30,
    )

    result = resp.json()
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return f"요약 실패: {result.get('error', {}).get('message', '알 수 없는 오류')}"


def send_telegram(message):
    """텔레그램으로 메시지 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })
    return resp.json()


def main():
    if not TELEGRAM_CHAT_ID:
        print("TELEGRAM_CHAT_ID가 설정되지 않았습니다.")
        return

    last_videos = load_last_videos()

    for channel_name, channel_id in CHANNELS:
        try:
            videos = get_latest_videos(channel_id)
            if not videos:
                continue

            latest = videos[0]
            last_id = last_videos.get(channel_id)

            if latest["id"] == last_id:
                print(f"[{channel_name}] 새 영상 없음")
                continue

            # 새 영상 발견
            print(f"[{channel_name}] 새 영상 발견: {latest['title']}")

            # 자막 가져오기
            transcript = get_transcript(latest["id"])
            if transcript:
                print(f"  자막 길이: {len(transcript)}자")
            else:
                print("  자막 없음 - 제목 기반 요약")

            # GPT-4o-mini로 요약
            summary = summarize_with_openai(latest["title"], transcript)

            # 메시지 구성
            msg = (
                f"🎬 <b>{channel_name}</b> 새 영상!\n"
                f"━━━━━━━━━━━━━━━\n"
                f"📌 <b>{latest['title']}</b>\n\n"
                f"📝 <b>요약</b>\n{summary}\n\n"
                f"🔗 {latest['link']}"
            )

            result = send_telegram(msg)
            print(f"  전송 결과: {result.get('ok')}")

            last_videos[channel_id] = latest["id"]

        except Exception as e:
            print(f"[{channel_name}] 오류: {e}")

    save_last_videos(last_videos)


if __name__ == "__main__":
    main()
