from pathlib import Path

from yogurt_social.router import StyleRouter
from yogurt_social.stickers import (
    StickerCatalogSnapshot,
    StickerRecord,
    StickerRetriever,
    StickerSendDecider,
)
from yogurt_social.stickers.models import StickerCandidate


def record(
    sticker_id: str,
    *,
    description: str,
    scenes=("playful",),
    emotions=("happy",),
    tones=("cute",),
    keywords=("可爱", "卖萌", "开心"),
    weight=1.0,
    confidence=0.9,
    avoid_when=(),
):
    return StickerRecord(
        id=sticker_id,
        file=f"images/{sticker_id}.webp",
        asset_path=Path(f"/tmp/{sticker_id}.webp"),
        sha256="0" * 64,
        status="approved",
        enabled=True,
        description=description,
        ocr_text="",
        scenes=tuple(scenes),
        emotions=tuple(emotions),
        tones=tuple(tones),
        keywords=tuple(keywords),
        relationships=("owner",),
        avoid_when=tuple(avoid_when),
        content_warnings=("none",),
        weight=weight,
        cooldown_seconds=1800,
        confidence=confidence,
    )


def snapshot(*items):
    return StickerCatalogSnapshot(
        schema_version="1.0",
        generated_at="now",
        generated_by="test",
        source_path=Path("/tmp/stickers.json"),
        asset_root=Path("/tmp"),
        stickers=tuple(items),
    )


def test_exact_semantics_beat_scene_and_weight():
    generic = record(
        "generic",
        description="普通可爱卖萌",
        scenes=("playful",),
        emotions=("happy",),
        keywords=("可爱", "卖萌", "开心"),
        weight=1.5,
    )
    speechless = record(
        "speechless",
        description="猫咪无语又尴尬，不知道说什么",
        scenes=("casual",),
        emotions=("speechless",),
        tones=("sarcastic",),
        keywords=("无语", "离谱", "不知道说什么"),
        weight=0.7,
    )
    candidates = StickerRetriever(snapshot(generic, speechless), minimum_score=5).retrieve(
        message="这也太离谱了吧",
        scene="playful",
        mood="speechless",
        relationship="owner",
        allow_sticker=True,
    )
    assert candidates[0].sticker_id == "speechless"
    assert dict(candidates[0].score_breakdown)["keyword_user"] == 5.0
    assert abs(dict(candidates[1].score_breakdown)["weight_prior"]) <= 0.5


def test_specific_explicit_request_does_not_force_unrelated_fallback():
    generic = record("generic", description="普通开心", keywords=("开心", "可爱", "卖萌"))
    candidates = StickerRetriever(snapshot(generic), minimum_score=5).retrieve(
        message="发一个生气的表情包",
        scene="playful",
        mood="neutral",
        relationship="owner",
        allow_sticker=True,
        explicit_request=True,
    )
    assert candidates == []


def test_generic_explicit_request_can_choose_safe_context_candidate():
    generic = record("generic", description="普通开心")
    candidates = StickerRetriever(snapshot(generic), minimum_score=5).retrieve(
        message="随便发个表情包",
        scene="casual",
        mood="neutral",
        relationship="owner",
        allow_sticker=True,
        explicit_request=True,
    )
    assert candidates and candidates[0].score >= 5
    assert "intent:explicit_generic" in candidates[0].matched_signals


def test_comfort_channel_is_open_but_high_risk_is_blocked():
    state = StyleRouter().analyze("我今天真的好累", relationship="owner")
    assert state.scene.value == "comfort"
    assert state.use_sticker is True
    comfort = record(
        "hug",
        description="温柔抱抱和陪伴",
        scenes=("comfort", "casual"),
        emotions=("tired", "warm"),
        tones=("warm",),
        keywords=("好累", "抱抱", "辛苦"),
    )
    candidates = StickerRetriever(snapshot(comfort), minimum_score=5).retrieve(
        message="我今天真的好累",
        assistant_reply="辛苦主人啦，让我抱抱你。",
        scene="comfort",
        mood="tired",
        relationship="owner",
        allow_sticker=True,
    )
    decision = StickerSendDecider().decide(
        user_message="我今天真的好累",
        assistant_reply="辛苦主人啦，让我抱抱你。",
        scene="comfort",
        mood="tired",
        explicit_request=False,
        candidates=candidates,
        phase="post_reply",
    )
    assert decision.should_send is True
    blocked = StickerSendDecider().decide(
        user_message="我不想活了",
        assistant_reply="我在这里陪你。",
        scene="casual",
        mood="neutral",
        explicit_request=True,
        candidates=candidates,
        phase="post_reply",
    )
    assert blocked.should_send is False
    assert blocked.mode == "safety_block"
    assert "high_risk_comfort" in blocked.reasons


def test_casual_is_eligible_but_neutral_small_talk_stays_quiet():
    state = StyleRouter().analyze("今天路上人挺多的", relationship="owner")
    assert state.scene.value == "casual"
    assert state.use_sticker is True
    generic = record(
        "casual",
        description="普通日常回应",
        scenes=("casual",),
        emotions=("neutral",),
        tones=("cute",),
        keywords=("回来", "早安", "晚安"),
    )
    candidates = StickerRetriever(snapshot(generic), minimum_score=5).retrieve(
        message="今天路上人挺多的",
        assistant_reply="是呀，今天外面看起来挺热闹。",
        scene="casual",
        mood="neutral",
        relationship="owner",
        allow_sticker=True,
    )
    decision = StickerSendDecider().decide(
        user_message="今天路上人挺多的",
        assistant_reply="是呀，今天外面看起来挺热闹。",
        scene="casual",
        mood="neutral",
        explicit_request=False,
        candidates=candidates,
        phase="post_reply",
    )
    assert decision.should_send is False


def test_expanded_celebration_phrases_trigger_in_casual_scene():
    candidates = [
        StickerCandidate(
            sticker_id="celebrate-top",
            file="images/celebrate-top.webp",
            description="开心庆祝",
            score=13.1,
            scenes=("casual", "celebration"),
            emotions=("happy",),
            tones=("cute",),
            matched_signals=("keyword_user",),
        ),
        StickerCandidate(
            sticker_id="celebrate-runner-up",
            file="images/celebrate-runner-up.webp",
            description="鼓掌夸奖",
            score=10.6,
            scenes=("casual", "celebration"),
            emotions=("happy",),
            tones=("cute",),
            matched_signals=("semantic",),
        ),
    ]
    decision = StickerSendDecider().decide(
        user_message="终于把这些功能都弄好了，我真的太开心了，快夸夸我！",
        assistant_reply="主人真的太厉害啦喵！",
        scene="casual",
        mood="neutral",
        explicit_request=False,
        candidates=candidates,
        phase="post_reply",
    )
    assert decision.should_send is True
    assert "celebration" in decision.intents
    assert "intent:celebration:3.0" in decision.reasons


def test_technical_is_hard_blocked():
    candidate = record(
        "tech",
        description="技术卡住",
        scenes=("technical",),
        keywords=("报错", "代码", "卡住"),
    )
    candidates = StickerRetriever(snapshot(candidate)).retrieve(
        message="接口报错了",
        assistant_reply="请运行下面的命令检查配置。",
        scene="technical",
        mood="frustrated",
        relationship="owner",
        allow_sticker=True,
    )
    assert candidates
    decision = StickerSendDecider().decide(
        user_message="接口报错了",
        assistant_reply="请运行下面的命令检查配置。",
        scene="technical",
        mood="frustrated",
        explicit_request=False,
        candidates=candidates,
        phase="post_reply",
    )
    assert decision.should_send is False
