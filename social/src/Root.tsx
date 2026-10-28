import { Composition } from "remotion";
import { sample } from "./sample";
import { FPS, HEIGHT, WIDTH, totalFrames, type Storyboard } from "./types";
import { SocialVideo } from "./Video";

export function Root() {
  return (
    <Composition
      id="PjeSocial"
      component={SocialVideo as unknown as React.FC<Record<string, unknown>>}
      width={WIDTH}
      height={HEIGHT}
      fps={FPS}
      durationInFrames={totalFrames(sample)}
      defaultProps={sample as unknown as Record<string, unknown>}
      calculateMetadata={({ props }) => ({ durationInFrames: totalFrames(props as unknown as Storyboard) })}
    />
  );
}
