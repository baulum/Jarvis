// Only used by `npm run studio`; the Core renders through render.mjs (Node API) with the same settings.
import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setCodec("h264");
