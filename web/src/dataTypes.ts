import { useQuery } from "@tanstack/react-query";
import { type DataType, fetchDataTypes } from "./api";

// 全部数据类型。queryKey 与媒体库页共用，避免重复请求。
export function useDataTypes() {
  return useQuery({ queryKey: ["data-types"], queryFn: fetchDataTypes });
}

// 剧集类结构（电视剧、动漫等），一个条目是「作品 + 季 + 集」。
export function isSeriesType(types: DataType[] | undefined, dataTypeId: string): boolean {
  return types?.find((type) => type.id === dataTypeId)?.structure === "series";
}

// 插件提供的数据类型：用来源插件匹配，不走 TMDB，不写 NFO、不整理。
export function isPluginType(types: DataType[] | undefined, dataTypeId: string): boolean {
  const pluginId = types?.find((type) => type.id === dataTypeId)?.plugin_id;
  return pluginId !== undefined && pluginId !== null;
}

// 条目数量的单位：剧集类写「部剧」或「部动漫」，Galgame 写「款游戏」，其余写「部电影」。
export function itemUnit(types: DataType[] | undefined, dataTypeId: string): string {
  if (dataTypeId === "galgame") {
    return "款游戏";
  }
  if (isPluginType(types, dataTypeId)) {
    return "个条目";
  }
  if (!isSeriesType(types, dataTypeId)) {
    return "部电影";
  }
  return dataTypeId === "anime" ? "部动漫" : "部剧";
}

// 插件类型的库按文件夹认条目，文件数量是文件夹数。
export function fileUnit(types: DataType[] | undefined, dataTypeId: string): string {
  return isPluginType(types, dataTypeId) ? "个文件夹" : "个视频";
}
