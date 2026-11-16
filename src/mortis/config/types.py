from mortis.config.models import AppConfig, TypeSection


def type_config(config: AppConfig, data_type_id: str) -> TypeSection:
    """每种数据类型在设置文件里有自己的一节。

    内置三种用各自的模型；插件类型用 PluginTypeConfig，没写过时是全默认值。
    """
    types = config.types
    if data_type_id == "movie":
        return types.movie
    if data_type_id == "tv":
        return types.tv
    if data_type_id == "anime":
        return types.anime
    return types.plugin(data_type_id)
