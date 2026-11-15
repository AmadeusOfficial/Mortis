import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { type TaskDetail, fetchTask, isTaskBusy } from "./api";

/** 盯住一个任务（连同它的子任务），全部结束时回调一次。 */
export function useTaskWatcher(onDone: (task: TaskDetail) => void) {
  const [taskId, setTaskId] = useState<number | null>(null);
  const callback = useRef(onDone);
  useEffect(() => {
    callback.current = onDone;
  });
  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => fetchTask(taskId as number),
    enabled: taskId !== null,
    refetchInterval: (query) =>
      query.state.data === undefined || isTaskBusy(query.state.data) ? 1000 : false,
  });
  useEffect(() => {
    if (taskId !== null && task.data?.id === taskId && !isTaskBusy(task.data)) {
      setTaskId(null);
      callback.current(task.data);
    }
  }, [taskId, task.data]);
  return { watch: setTaskId, watching: taskId !== null, task: task.data };
}
