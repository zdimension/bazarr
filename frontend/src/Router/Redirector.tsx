import { FunctionComponent, useEffect } from "react";
import { useNavigate } from "react-router";
import { LoadingOverlay } from "@mantine/core";
import { useSystemSettings } from "@/apis/hooks";

const Redirector: FunctionComponent = () => {
  const { data } = useSystemSettings();

  const navigate = useNavigate();

  useEffect(() => {
    if (data) {
      const {
        use_sonarr: useSonarr,
        use_radarr: useRadarr,
        library_source: librarySource,
      } = data.general;
      if (librarySource === "plex") {
        if (data.plex.series_library_ids?.length) {
          navigate("/series");
        } else if (data.plex.movie_library_ids?.length) {
          navigate("/movies");
        } else {
          navigate("/settings/integrations/plex");
        }
        return;
      }
      if (useSonarr) {
        navigate("/series");
      } else if (useRadarr) {
        navigate("/movies");
      } else {
        navigate("/settings/application/general");
      }
    }
  }, [data, navigate]);

  return <LoadingOverlay visible></LoadingOverlay>;
};

export default Redirector;
