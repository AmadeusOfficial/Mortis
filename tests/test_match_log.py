"""一份真实扫描日志里的片名：中文搜索语言下用英文、罗马音文件名识别电影。

TMDB 的返回录在 fixtures/tmdb/match_log.json，只留打分用得到的字段。
"""

import json

import httpx2 as httpx
import pytest

from mortis.organize.names import parse_name
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.search import find_best
from tests.fakes import FIXTURES

THRESHOLD = 0.75
RECORDED = json.loads((FIXTURES / "match_log.json").read_text(encoding="utf-8"))

# 名字 → 应该认成的 TMDB 编号；None 是 TMDB 上没有这个名字，应该认不出。
EXPECTED: dict[str, int | None] = {
    "100.Yen.Love": 314606,
    "100.yen.love.2014.1080p.bluray.x264-regret": 314606,
    "A.Chinese.Odyssey.Part.1": 13345,
    "A.Chinese.Odyssey.Part.2": 21835,
    "A.Guilty.Conscience": 990326,
    "All's.Well.End's.Well": 60145,
    "All.Quiet.on.the.Western.Front": 49046,
    "Buffalo.'66": 9464,
    "Chang.An": 1089654,
    "Chungking.Express": 11104,
    "CJ7": 13688,
    "Creation.of.the.Gods.I.Kingdom.of.Storms": 856289,
    "Dear.You": 1671548,
    "Dungeons.and.Dragons.Honor.Among.Thieves": 493529,
    "Evangelion[3.0+1.01]Thrice.Upon.a.Time": 283566,
    "Fight.Back.to.School": 47647,
    "Fight.Back.to.School.2": 53165,
    "Fight.Back.to.School.3": 66657,
    "Final.Justice": 69727,
    "Flirting.Scholar": 37703,
    "Flow": 823219,
    "Forbidden.City.Cop": 37702,
    "From.Beijing.with.Love": 41387,
    "G.for.Gap": None,
    "God.of.Gamblers.II": 57663,
    "God.of.Gamblers.III.Back.to.Shanghai": 53658,
    "Goodbye.Mr.Lose": 362682,
    "Gorgeous.UNCUT": 10951,
    "Hagoku": 455806,
    "Haikyuu!!.Concept.no.Tatakai.1080p": 461763,
    "Haikyuu!!.Owari.to.Hajimari.1080p": 347183,
    "Haikyuu!!.Sainou.to.Sense.1080p": 461762,
    "Haikyuu!!.Shousha.to.Haisha.1080p": 347186,
    "Hail.the.Judge": 55156,
    "Hello.Mr.Tree": 90283,
    "I.Saw.The.Devil": 49797,
    "Infernal.Affairs.2002.CC.BluRay.1080p.2Audio.DTS-HD.MA.5.1.x265.10bit-ALT": 10775,
    "Infernal.Affairs.II.2003.CC.BluRay.1080p.2Audio.DTS-HD.MA.5.1.x265.10bit-ALT": 11647,
    "Infernal.Affairs.III.2003.CC.BluRay.1080p.2Audio.DTS-HD.MA.5.1.x265.10bit-ALT": 14310,
    "Johnny.Keep.Walking": 1173076,
    "Journey.to.the.West.Conquering.the.Demons": 170657,
    "Journey.to.the.West.The.Demons.Strike.Back": 435800,
    "Justice.My.Foot": 32517,
    "Kaibutsu": 1050035,
    "Kim.Ji-young.Born.1982": 586048,
    "King.of.Beggars": 47648,
    "Kung.Fu.Hustle": 9470,
    "L'ultima.volta.che.siamo.stati.bambini": 1080936,
    "Lang.Lang.Shan.Xiao.Yao.Guai": 1304434,
    "Le.Grand.Méchant.Renard.et.autres.contes": 453191,
    "Legend.of.the.Dragon": 56118,
    "Look.Out.Officer": 138960,
    "Love.is.Love": 173653,
    "Love.on.Delivery": 53163,
    "Mad.Max.Fury.Road": 76341,
    "Millennium.Actress": 33320,
    "[哪吒之魔童闹海].Ne.Zha.2.2025.2160p.HQ.WEB-DL.H265.10bit.DDP.5.1.Atmos-CMCTV": 980477,
    "Ne.Zha.2019.CHINESE.2160p.BluRay.x264.8bit.SDR.DTS-HD.MA.5.1-SWTYBLZ": 615453,
    "Never.Say.Never": 1112175,
    "No.More.Bets": 1121612,
    "Nobody.Knows": 2517,
    "Old.Fox": 1006012,
    "One.Cut.of.the.Dead": 513434,
    "Out.of.the.Dark": 45452,
    "Paprika": 4977,
    "Pegasus": 575219,
    "Pegasus2": 1228891,
    "Pegasus3": 1462229,
    "Robot.Dreams": 838240,
    "Royal.Tramp": 51730,
    "Royal.Tramp.II": 51731,
    "Missing.2023.2160p.iTunes.WEB-DL.DD.5.1.HDR10.H.265-DreamHD": 768362,
    "Searching.2018.BluRay.1080p.DTS.x265.10bit-Xiaomi": 489999,
    "Shaolin.Soccer": 11770,
    "Sixty.Million.Dollar.Man": 52324,
    "The.Battle.at.Lake.Changjin": 779029,
    "The.Battle.At.Lake.Changjin.II": 928123,
    "The.Legend.of.Hei": 620249,
    "The.Legend.of.Hei2": 1144107,
    "The.Mad.Monk": 75197,
    "The.Mermaid": 381890,
    "The.Pig.the.Snake.and.the.Pigeon": 1046090,
    "The.Shadow's.Edge": 1419406,
    "The.Sparring.Partner": 1005251,
    "The.Super.Mario.Bros.Movie": 502356,
    "The.Super.Mario.Galaxy.Movie": 1226863,
    "Tricky.Brains": 41364,
    "Warriors.of.the.Rainbow.Seediq.Bale.Part.1.The.Sun.Flag": 98344,
    "Warriors.of.the.Rainbow.Seediq.Bale.Part.2.The.Rainbow.Bridge": 313230,
    "When.Fortune.Smiles": 135061,
}


def _replay(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/3")
    params = {k: v for k, v in request.url.params.items() if k != "api_key"}
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    if key not in RECORDED:
        pytest.fail(f"没有录下这个请求：{key}")
    return httpx.Response(200, json=RECORDED[key])


@pytest.fixture(scope="module")
def tmdb() -> TmdbClient:
    return TmdbClient(
        httpx.Client(transport=httpx.MockTransport(_replay)),
        "k",
        language="zh-CN",
        include_adult=False,
    )


# 没有年份、TMDB 上有好几部名字完全一样的片：不自动绑定，正确的那部要在候选里。
AMBIGUOUS = {
    "A.Guilty.Conscience",
    "All's.Well.End's.Well",
    "All.Quiet.on.the.Western.Front",
    "Dear.You",
    "Fight.Back.to.School",
    "Final.Justice",
    "Flirting.Scholar",
    "Flow",
    "Gorgeous.UNCUT",
    "Kaibutsu",
    "King.of.Beggars",
    "Legend.of.the.Dragon",
    "Love.is.Love",
    "Never.Say.Never",
    "Nobody.Knows",
    "Out.of.the.Dark",
    "Paprika",
    "Pegasus",
    "Royal.Tramp",
    "The.Mad.Monk",
    "The.Mermaid",
}


@pytest.mark.parametrize(("name", "tmdb_id"), EXPECTED.items(), ids=list(EXPECTED))
def test_log_name_is_matched(tmdb: TmdbClient, name: str, tmdb_id: int | None) -> None:
    match = find_best(tmdb, parse_name(name), threshold=THRESHOLD)
    if name in AMBIGUOUS:
        assert match is not None and match.rivals
        assert tmdb_id in {hit.tmdb_id for hit in (match.hit, *match.rivals)}
        return
    matched = match is not None and match.score >= THRESHOLD and not match.rivals
    got = match.hit.tmdb_id if matched else None
    assert got == tmdb_id, match and [(r.tmdb_id, r.title, r.year) for r in match.rivals]


def test_english_only_winner_gets_local_title(tmdb: TmdbClient) -> None:
    match = find_best(tmdb, parse_name("Pegasus"), threshold=THRESHOLD)
    assert match is not None and match.hit.title == "飞驰人生"
