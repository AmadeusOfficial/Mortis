import { Alert, Button, Checkbox, Loader, Popover, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type DragEvent, useState } from "react";
import { type SortOrder, type Genre, type WallSort, fetchShelves } from "../../api";
import { IconArrowRight, IconDrag, IconList } from "../../icons";
import { PosterCard } from "../PosterCard";
import styles from "./TagShelves.module.css";

export type WallView = "wall" | "shelves";

const viewKey = (libraryId: number) => `mortis-wall-view-${libraryId}`;
const shelfKey = (libraryId: number) => `mortis-wall-shelves-${libraryId}`;

export function readView(libraryId: number): WallView {
  return window.localStorage.getItem(viewKey(libraryId)) === "shelves" ? "shelves" : "wall";
}

export function saveView(libraryId: number, view: WallView) {
  window.localStorage.setItem(viewKey(libraryId), view);
}

// 勾选的类型 id，按显示顺序。
export function readShelfTags(libraryId: number): number[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(shelfKey(libraryId)) ?? "[]");
    return Array.isArray(saved) ? saved.filter((value): value is number => Number.isInteger(value)) : [];
  } catch {
    return [];
  }
}

export function saveShelfTags(libraryId: number, ids: number[]) {
  window.localStorage.setItem(shelfKey(libraryId), JSON.stringify(ids));
}

// 「展示」面板：勾选要显示的类型；已勾选的在上面，拖把手排顺序；未勾选的按名称排在下面。
export function ShelfPicker({
  tags,
  selected,
  onChange,
}: {
  tags: Genre[];
  selected: number[];
  onChange: (ids: number[]) => void;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const byId = new Map(tags.map((tag) => [tag.id, tag]));
  const picked = selected.flatMap((id) => byId.get(id) ?? []);
  const rest = tags
    .filter((tag) => !selected.includes(tag.id))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));

  // 拖到哪一行上，就占住那一行的位置。
  const moveTo = (targetId: number) => {
    if (dragging === null || dragging === targetId) {
      return;
    }
    const ids = picked.map((tag) => tag.id);
    const to = ids.indexOf(targetId);
    ids.splice(ids.indexOf(dragging), 1);
    ids.splice(to, 0, dragging);
    onChange(ids);
  };
  const startDrag = (event: DragEvent<HTMLSpanElement>, id: number) => {
    setDragging(id);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(id));
    const row = event.currentTarget.closest("li");
    if (row !== null) {
      event.dataTransfer.setDragImage(row, 12, 12);
    }
  };

  return (
    <Popover position="bottom-end" width={300} shadow="md" withinPortal>
      <Popover.Target>
        <Button variant="default" size="sm" leftSection={<IconList size={14} />}>
          展示
        </Button>
      </Popover.Target>
      <Popover.Dropdown p={6}>
        {tags.length === 0 ? (
          <Text size="sm" c="var(--mo-dim)" p={8}>
            这个库还没有类型，刮削后会带上。
          </Text>
        ) : (
          <ul className={styles.pickList} aria-label="要显示的类型">
            {picked.map((tag) => (
              <li
                key={tag.id}
                className={styles.pickRow}
                data-dragging={dragging === tag.id || undefined}
                onDragOver={(event) => {
                  if (dragging !== null) {
                    event.preventDefault();
                    moveTo(tag.id);
                  }
                }}
                onDrop={(event) => event.preventDefault()}
              >
                <span
                  className={styles.handle}
                  draggable
                  aria-label={`拖动排序：${tag.name}`}
                  onDragStart={(event) => startDrag(event, tag.id)}
                  onDragEnd={() => setDragging(null)}
                >
                  <IconDrag size={14} />
                </span>
                <Checkbox
                  size="xs"
                  checked
                  label={tag.name}
                  onChange={() => onChange(selected.filter((id) => id !== tag.id))}
                  className={styles.pickCheck}
                />
                <span className={`${styles.pickCount} mo-mono`}>{tag.count}</span>
              </li>
            ))}
            {rest.map((tag) => (
              <li key={tag.id} className={styles.pickRow}>
                <span className={styles.handle} data-disabled />
                <Checkbox
                  size="xs"
                  checked={false}
                  label={tag.name}
                  onChange={() => onChange([...picked.map((item) => item.id), tag.id])}
                  className={styles.pickCheck}
                />
                <span className={`${styles.pickCount} mo-mono`}>{tag.count}</span>
              </li>
            ))}
          </ul>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}

// 分类视图：每个勾选的类型一行，横向滚动；没落在任何已选类型里的条目不显示。
export function TagShelves({
  libraryId,
  tagIds,
  sort,
  order,
  unit,
  onViewAll,
}: {
  libraryId: number;
  tagIds: number[];
  sort: WallSort;
  order: SortOrder;
  unit: string;
  onViewAll: (tag: { id: number; name: string }) => void;
}) {
  const shelves = useQuery({
    queryKey: ["wall", libraryId, "shelves", tagIds, sort, order],
    queryFn: () => fetchShelves(libraryId, tagIds, sort, order),
    enabled: tagIds.length > 0,
  });

  if (tagIds.length === 0) {
    return (
      <div className={styles.empty}>
        <Text c="var(--mo-dim)" size="sm">
          点「展示」选择要显示的类型。
        </Text>
      </div>
    );
  }
  if (shelves.isPending) {
    return <Loader size="sm" />;
  }
  if (shelves.isError) {
    return (
      <Alert color="red" title="读不到分类">
        {shelves.error.message}
      </Alert>
    );
  }
  return (
    <div className={styles.shelves}>
      {shelves.data.map((shelf) => (
        <section key={shelf.tag_id} className={styles.shelf} aria-label={shelf.name}>
          <div className={styles.shelfHead}>
            <h3 className={styles.shelfTitle}>
              <span className={styles.shelfName}>{shelf.name}</span>
              <span className={styles.shelfCount} title={`${shelf.count} ${unit}`}>
                {shelf.count}
              </span>
            </h3>
            {shelf.count > 0 && (
              <button
                type="button"
                className={styles.viewAll}
                aria-label={`查看全部：${shelf.name}`}
                onClick={() => onViewAll({ id: shelf.tag_id, name: shelf.name })}
              >
                <span>查看全部</span>
                <IconArrowRight size={13} />
              </button>
            )}
          </div>
          {shelf.items.length === 0 ? (
            <Text size="sm" c="var(--mo-dim)">
              这个类型下还没有内容。
            </Text>
          ) : (
            <div className={styles.row}>
              {shelf.items.map((item) => (
                <div key={item.id} className={styles.rowItem}>
                  <PosterCard item={item} />
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
