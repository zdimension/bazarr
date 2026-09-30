import { useSystemSettings } from ".";

export const useEnabledStatus = () => {
  const { data } = useSystemSettings();
  const plex = data?.general.library_source === "plex";

  return {
    sonarr: plex
      ? Boolean(data?.plex.series_library_ids?.length)
      : (data?.general.use_sonarr ?? false),
    radarr: plex
      ? Boolean(data?.plex.movie_library_ids?.length)
      : (data?.general.use_radarr ?? false),
  };
};

export const useShowOnlyDesired = () => {
  const { data } = useSystemSettings();
  return data?.general.embedded_subs_show_desired ?? false;
};

export const useInstanceName = () => {
  const { data } = useSystemSettings();
  return data?.general.instance_name;
};
