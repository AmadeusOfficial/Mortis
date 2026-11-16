from mortis.config.models import AppConfig, MovieScrapeConfig, TvScrapeConfig


def type_config(config: AppConfig, data_type_id: str) -> MovieScrapeConfig | TvScrapeConfig:
    """每种内置数据类型在设置文件里有自己的一节。动漫和电视剧同一种结构，用同一种设置。"""
    if data_type_id == "tv":
        return config.tv
    if data_type_id == "anime":
        return config.anime
    return config.movie
