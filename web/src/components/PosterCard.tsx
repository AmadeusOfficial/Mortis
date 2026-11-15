import { AspectRatio, Badge, Card, Center, Image, Text } from "@mantine/core";
import { Link } from "react-router";
import type { WallItem } from "../api";

export function PosterCard({ item }: { item: WallItem }) {
  const caption = [item.subtitle, item.year].filter((part) => part !== null).join(" · ");
  return (
    <Card component={Link} to={`/items/${item.id}`} padding="xs" withBorder>
      <Card.Section>
        <AspectRatio ratio={2 / 3}>
          {item.poster_url !== null ? (
            <Image src={item.poster_url} alt={item.title} fit="cover" />
          ) : (
            <Center bg="gray.1" p="sm">
              <Text size="sm" c="dimmed" ta="center">
                {item.title}
              </Text>
            </Center>
          )}
        </AspectRatio>
      </Card.Section>
      <Text size="sm" fw={500} mt="xs" lineClamp={1} title={item.title}>
        {item.title}
      </Text>
      <Text size="xs" c="dimmed" lineClamp={1}>
        {caption || "\u00a0"}
      </Text>
      {!item.is_scraped && (
        <Badge size="xs" variant="light" color="gray" mt={4}>
          未刮削
        </Badge>
      )}
    </Card>
  );
}
