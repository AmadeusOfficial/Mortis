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

// 条目数量的单位按数据结构：剧集类写「部剧」或「部动漫」，其余写「部电影」。
export function itemUnit(types: DataType[] | undefined, dataTypeId: string): string {
  if (!isSeriesType(types, dataTypeId)) {
    return "部电影";
  }
  return dataTypeId === "anime" ? "部动漫" : "部剧";
}
