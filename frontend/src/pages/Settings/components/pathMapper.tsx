import { FunctionComponent, useCallback, useMemo } from "react";
import { Button, Code, List, Stack, Text, TextInput } from "@mantine/core";
import { faArrowCircleRight, faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { capitalize } from "lodash";
import { Action, FileBrowser } from "@/components";
import { AppColumnDef as ColumnDef } from "@/components/tables/features";
import SimpleTable from "@/components/tables/SimpleTable";
import {
  moviesEnabledKey,
  pathMappingsKey,
  pathMappingsMovieKey,
  seriesEnabledKey,
} from "@/pages/Settings/keys";
import { useFormActions } from "@/pages/Settings/utilities/FormValues";
import { useSettingValue } from "@/pages/Settings/utilities/hooks";
import { useArrayAction } from "@/utilities";
import { Message } from "./Message";

type SupportType = "sonarr" | "radarr" | "plex-series" | "plex-movies";

const isSeries = (type: SupportType) =>
  type === "sonarr" || type === "plex-series";

const isPlex = (type: SupportType) => type.startsWith("plex-");

const getSupportName = (type: SupportType) =>
  isPlex(type) ? "Plex" : capitalize(type);

const getSupportKey = (type: SupportType) => {
  if (isSeries(type)) {
    return pathMappingsKey;
  } else {
    return pathMappingsMovieKey;
  }
};

const getEnabledKey = (type: SupportType) => {
  if (isSeries(type)) {
    return seriesEnabledKey;
  } else {
    return moviesEnabledKey;
  }
};

interface PathMappingItem {
  from: string;
  to: string;
}

interface TableProps {
  type: SupportType;
}

const PathMappingHelp: FunctionComponent<TableProps> = ({ type }) => {
  const support = getSupportName(type);
  const noun = isSeries(type) ? "series" : "movies";
  const windowsPath = isSeries(type) ? "D:\\TV" : "D:\\Movies";
  const bazarrPath = isSeries(type) ? "/data/tv" : "/data/movies";

  return (
    <Stack gap="xs" data-testid="path-mapping-help">
      <Message>
        Path mappings translate the file paths reported by {support} into paths
        that Bazarr can access on this machine. Each row is a simple
        substitution: when a path received from {support} contains the value in
        the {support} column, that part is replaced by the value in the Bazarr
        column.
      </Message>
      <Text size="sm" fw={600}>
        Use path mappings when
      </Text>
      <List size="sm" c="dimmed" withPadding>
        <List.Item>
          Bazarr and {support} run on different hosts, or in containers with
          different volume mounts, so the same library is visible at different
          paths.
        </List.Item>
        <List.Item>
          A path reported by {support} points somewhere Bazarr cannot open — for
          example {support} runs on Windows and reports{" "}
          <Code>{windowsPath}</Code>, while Bazarr runs on Linux and the same
          files live under <Code>{bazarrPath}</Code>.
        </List.Item>
      </List>
      <Text size="sm" fw={600}>
        Don&rsquo;t use path mappings when
      </Text>
      <Message>
        Bazarr and {support} already share the same filesystem and identical
        paths. If Bazarr can open your {noun} using the paths reported by{" "}
        {support}, leave this list empty — unnecessary mappings can prevent
        Bazarr from finding your files.
      </Message>
    </Stack>
  );
};

export const PathMappingTable: FunctionComponent<TableProps> = ({ type }) => {
  const key = getSupportKey(type);
  const items = useSettingValue<[string, string][]>(key);

  const enabledKey = getEnabledKey(type);
  const supportEnabled = useSettingValue<boolean>(enabledKey, {
    original: true,
  });
  const librarySource = useSettingValue<string>(
    "settings-general-library_source",
    {
      original: true,
    },
  );
  const enabled = isPlex(type) ? librarySource === "plex" : supportEnabled;
  const support = getSupportName(type);
  const browserType = isSeries(type) ? "sonarr" : "radarr";

  const { setValue } = useFormActions();

  const updateRow = useCallback(
    (newItems: PathMappingItem[]) => {
      setValue(
        newItems.map((v) => [v.from, v.to]),
        key,
      );
    },
    [key, setValue],
  );

  const addRow = useCallback(() => {
    if (items) {
      const newItems = [...items, ["", ""]];
      setValue(newItems, key);
    }
  }, [items, key, setValue]);

  const data = useMemo<PathMappingItem[]>(
    () => items?.map((v) => ({ from: v[0], to: v[1] })) ?? [],
    [items],
  );

  const action = useArrayAction<PathMappingItem>((fn) => {
    updateRow(fn(data));
  });

  const columns = useMemo<ColumnDef<PathMappingItem>[]>(
    () => [
      {
        header: support,
        accessorKey: "from",
        cell: ({ row: { original, index } }) => {
          if (isPlex(type)) {
            // Plex does not expose a filesystem-browser API. This is the
            // source path shown by Plex for the media part, so it is entered
            // directly while the Bazarr destination can still be browsed.
            return (
              <TextInput
                placeholder="Path reported by Plex"
                value={original.from}
                onChange={(event) => {
                  action.mutate(index, {
                    ...original,
                    from: event.currentTarget.value,
                  });
                }}
              ></TextInput>
            );
          }
          return (
            <FileBrowser
              type={browserType}
              defaultValue={original.from}
              onChange={(path) => {
                action.mutate(index, { ...original, from: path });
              }}
            ></FileBrowser>
          );
        },
      },
      {
        id: "arrow",
        cell: () => (
          <FontAwesomeIcon icon={faArrowCircleRight}></FontAwesomeIcon>
        ),
      },
      {
        header: "Bazarr",
        accessorKey: "to",
        cell: ({ row: { original, index } }) => {
          return (
            <FileBrowser
              defaultValue={original.to}
              type="bazarr"
              onChange={(path) => {
                action.mutate(index, { ...original, to: path });
              }}
            ></FileBrowser>
          );
        },
      },
      {
        id: "action",
        accessorKey: "to",
        cell: ({ row: { index } }) => {
          return (
            <Action
              label="Remove"
              icon={faTrash}
              onClick={() => action.remove(index)}
            ></Action>
          );
        },
      },
    ],
    [action, browserType, support, type],
  );

  return (
    <Stack gap="sm">
      <PathMappingHelp type={type}></PathMappingHelp>
      {enabled ? (
        <>
          <SimpleTable
            tableStyles={{ emptyText: "No mapping" }}
            columns={columns}
            data={data}
          ></SimpleTable>
          <Button fullWidth onClick={addRow}>
            Add
          </Button>
        </>
      ) : (
        <Message>
          Path Mappings will be available after staged changes are saved
        </Message>
      )}
    </Stack>
  );
};
