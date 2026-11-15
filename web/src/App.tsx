import { Alert, Center, Loader, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { fetchSystemInfo } from "./api";

export function App() {
  const info = useQuery({ queryKey: ["system-info"], queryFn: fetchSystemInfo });

  return (
    <Center h="100vh">
      <Stack align="center" gap="xs">
        <Title order={1}>莫提斯</Title>
        <Text c="dimmed">智能体元数据刮削管理器</Text>
        {info.isPending && <Loader size="sm" />}
        {info.isError && (
          <Alert color="red" title="连不上后端">
            {info.error.message}
          </Alert>
        )}
        {info.isSuccess && (
          <>
            <Text size="sm">版本 {info.data.version}</Text>
            <Text size="sm" c="dimmed">
              数据目录 {info.data.data_dir}
            </Text>
          </>
        )}
      </Stack>
    </Center>
  );
}
