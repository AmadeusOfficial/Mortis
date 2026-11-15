import { Anchor, Avatar, Badge, Group, Image, Stack, Text } from "@mantine/core";
import type { FieldOut, ImageValue, PersonValue, RatingValue } from "../api";

const PEOPLE_SHOWN = 24;

export function FieldValue({ field }: { field: FieldOut }) {
  const { value } = field;
  if (value === null || value === undefined || (Array.isArray(value) && value.length === 0)) {
    return (
      <Text size="sm" c="dimmed">
        —
      </Text>
    );
  }
  switch (field.value_type) {
    case "tags":
      return (
        <Group gap={4}>
          {(value as string[]).map((tag) => (
            <Badge key={tag} variant="light">
              {tag}
            </Badge>
          ))}
        </Group>
      );
    case "people": {
      const people = value as PersonValue[];
      return (
        <Group gap="sm">
          {people.slice(0, PEOPLE_SHOWN).map((person) => (
            <Group key={person.id} gap={6} wrap="nowrap">
              <Avatar src={person.photo_url} alt={person.name} radius="xl" size="md">
                {person.name.slice(0, 1)}
              </Avatar>
              <div>
                <Text size="sm">{person.name}</Text>
                {person.character !== null && (
                  <Text size="xs" c="dimmed">
                    {person.character}
                  </Text>
                )}
              </div>
            </Group>
          ))}
          {people.length > PEOPLE_SHOWN && (
            <Text size="sm" c="dimmed">
              等 {people.length} 人
            </Text>
          )}
        </Group>
      );
    }
    case "rating":
      return (
        <Stack gap={0}>
          {(value as RatingValue[]).map((rating) => (
            <Text key={rating.source} size="sm">
              {rating.rating.toFixed(1)} / {rating.max_rating}（{rating.source.toUpperCase()}
              {rating.vote_count !== null && `，${rating.vote_count} 人`}）
            </Text>
          ))}
        </Stack>
      );
    case "images":
      return (
        <Group gap="xs">
          {(value as ImageValue[])
            .filter((image) => image.url !== null)
            .map((image) => (
              <Image
                key={image.url}
                src={image.url}
                alt={field.label}
                h={90}
                w="auto"
                fit="contain"
                radius="sm"
              />
            ))}
        </Group>
      );
    case "url":
      return (
        <Anchor href={String(value)} target="_blank" rel="noreferrer" size="sm">
          {String(value)}
        </Anchor>
      );
    case "longtext":
      return (
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
          {String(value)}
        </Text>
      );
    default:
      return <Text size="sm">{String(value)}</Text>;
  }
}
