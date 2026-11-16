from mortis.config.models import AppConfig, MovieScrapeConfig, TvScrapeConfig


def type_config(config: AppConfig, data_type_id: str) -> MovieScrapeConfig | TvScrapeConfig:
    """每种内置数据类型在设置文件里有自己的一节。"""
    return config.tv if data_type_id == "tv" else config.movie
