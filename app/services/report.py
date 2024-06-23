import io
import os
from datetime import date

import mecab_ko as MeCab
import pandas as pd
import numpy as np
from collections import Counter
from wordcloud import WordCloud
from matplotlib import font_manager
import matplotlib.pyplot as plt

import seaborn as sns

from fastapi import HTTPException

from app.db.query import SELECT_STT_DATA_BETWEEN_DATE, INSERT_IMAGE_FILES_META_DATA
from app.db.worker import execute_select_query, execute_insert_update_query

FONT_PATH = os.path.abspath("./NanumFontSetup_TTF_GOTHIC/NanumGothic.ttf")
font_prop = font_manager.FontProperties(fname=FONT_PATH)

font_manager.fontManager.addfont(FONT_PATH)
plt.rcParams["font.family"] = "NanumGothic"
plt.rcParams["axes.unicode_minus"] = False

POS_TAG_TO_KOREAN = {
    "NNP": "고유명사",
    "NNG": "명사",
    "NP": "대명사",
    "VV": "동사",
    "VA": "형용사",
    "MAG": "부사",
}


# 형태소 분석
def parse_text(text):
    """텍스트에 형태소분석 결과를 반환"""
    mecab = MeCab.Tagger()
    return mecab.parse(text)


def classify_words_by_pos(parsed_text):
    """파싱된 텍스트에서 품사별로 단어를 분류"""
    pos_lists = {korean: [] for korean in POS_TAG_TO_KOREAN.values()}
    pos_unique_lists = {korean: set() for korean in POS_TAG_TO_KOREAN.values()}

    for line in parsed_text.split("\n"):
        if "\t" not in line:
            continue
        word, tag_info = line.split("\t")
        tag = tag_info.split(",")[0]
        if tag in POS_TAG_TO_KOREAN:
            korean_pos = POS_TAG_TO_KOREAN[tag]
            pos_lists[korean_pos].append(word)
            pos_unique_lists[korean_pos].add(word)

    return pos_lists, pos_unique_lists


def build_pos_summary(
    pos_lists,
    pos_unique_lists,
):
    """품사별 단어 리스트와 고유 단어 세트에서 요약 정보를 구성하여 반환"""
    total_words = sum(len(words) for words in pos_lists.values())
    # total_unique_words = sum(
    #     len(unique_words) for unique_words in pos_unique_lists.values()
    # )
    summary = {
        pos: f"{len(words)} 개, {round(len(words) / total_words * 100, 1)}%"
        for pos, words in pos_lists.items()
    }
    summary["총단어 수"] = total_words
    return summary


def analyze_text_with_mecab(text):
    """품사별로 단어를 분류"""
    parsed_text = parse_text(text)
    pos_lists, pos_unique_lists = classify_words_by_pos(parsed_text)
    return build_pos_summary(pos_lists, pos_unique_lists)


def extract_speaker_data(data):
    """발화자별로 텍스트를 추출하여 하나의 문자열로 결합"""
    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame(data)
    speaker_data = (
        data.groupby("speaker")["text_edited"]
        .apply(lambda texts: " ".join(texts.astype(str)))
        .to_dict()
    )
    return speaker_data


def analyze_speech_data(morphs_data):
    """형태소분석"""
    speaker_data = extract_speaker_data(morphs_data)
    a = {
        speaker: analyze_text_with_mecab(text) for speaker, text in speaker_data.items()
    }
    return a


def create_morphs_data(user_id, start_date, end_date):
    params = {
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
    }

    morphs_data = execute_select_query(
        query=SELECT_STT_DATA_BETWEEN_DATE, params=params
    )

    morps_data = analyze_speech_data(morphs_data)
    return morps_data


# 워드클라우드


def create_circle_mask():
    """mask 생성"""
    x, y = np.ogrid[:600, :600]
    center_x, center_y = 300, 300
    radius = 300

    circle = (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2
    mask = 255 * np.ones((600, 600), dtype=np.uint8)
    mask[circle] = 0
    return mask


def extract_nouns_with_mecab(text):
    """형태소분석 명사 추출"""
    mecab = MeCab.Tagger()
    nouns = []
    for line in mecab.parse(text).split("\n"):
        if "\t" in line:
            word, tag_info = line.split("\t")
            tag = tag_info.split(",")[0]
            if tag in ["NNG", "NNP"]:
                nouns.append(word)
    return nouns


def count_words(nouns):
    """한 글자 명사 제거"""
    words = [word for word in nouns if len(word) > 1]
    return Counter(words)


def generate_wordcloud(word_counts, font_path, mask):
    """워드클라우드 기본 생성"""
    wc = WordCloud(
        font_path=font_path,
        background_color="white",
        width=800,
        height=800,
        max_words=200,
        max_font_size=100,
        colormap="viridis",
        mask=mask,
    )
    wc.generate_from_frequencies(word_counts)
    return wc


def save_wordcloud(wordcloud, image_path, speaker, font_prop):
    """워드클라우드를 S3 및 로컬에 저장"""
    try:
        # 메모리에 워드클라우드 이미지 저장
        img_data = io.BytesIO()
        plt.figure(figsize=(10, 10))
        plt.imshow(wordcloud, interpolation="bilinear")
        plt.axis("off")
        plt.text(
            0.05,
            0,
            speaker,
            fontsize=24,
            fontproperties=font_prop,
            ha="left",
            va="top",
            transform=plt.gca().transAxes,
            bbox=dict(facecolor="white", alpha=0.5),
        )

        plt.savefig(img_data, format="PNG", bbox_inches="tight")
        plt.savefig(image_path, format="PNG", bbox_inches="tight")
        plt.close()
        img_data.seek(0)

        return img_data
    except Exception as e:
        return {"error": str(e)}


def create_wordcloud(stt_data, font_path):
    """워드클라우드 생성 및 파일로 저장"""
    if not isinstance(stt_data, pd.DataFrame):
        stt_data = pd.DataFrame(stt_data)

    for speaker in stt_data["speaker_label"].unique():
        speaker_data = stt_data[stt_data["speaker_label"] == speaker]
        text = " ".join(speaker_data["text_edited"].astype(str))
        nouns = extract_nouns_with_mecab(text)
        word_counts = count_words(nouns)
        mask = create_circle_mask()
        wordcloud = generate_wordcloud(word_counts, font_path, mask)

        # 워드클라우드 저장
        img_data = save_wordcloud(
            wordcloud,
            speaker,
            font_prop,
        )
    return img_data


def create_image_metadata(
    image_id: str,
    speaker: str,
    user_id: str,
    start_date: date,
    end_date: date,
    type: str,
    image_path: str,
):
    return {
        "image_id": image_id,
        "speaker": speaker,
        "user_id": user_id,
        "start_date": start_date,
        "end_date": end_date,
        "type": type,
        "image_path": image_path,
    }


def insert_image_file_metadata(metadata: dict):
    execute_insert_update_query(query=INSERT_IMAGE_FILES_META_DATA, params=metadata)
