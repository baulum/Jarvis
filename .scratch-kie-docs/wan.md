# Wan 3.0 - Video

## OpenAPI Specification

```yaml
openapi: 3.0.1
info:
  title: ''
  description: ''
  version: 1.0.0
paths:
  /api/v1/jobs/createTask:
    post:
      summary: Wan 3.0 - Video
      deprecated: false
      description: >-
        ## Create Task

        Generate video through Wan 3.0. The standard model uses the parameter
        "model=wan/3-0-video".


        <Card title="Get Task Details" icon="lucide-search"
        href="/market/common/get-task-detail">
          After submission, use the unified query endpoint to check task progress and retrieve results
        </Card>


        ::: tip[]

        For production use, we recommend providing the `callBackUrl` parameter
        so your service can receive completion notifications instead of polling
        for task status.

        :::


        :::tip

        Billed seconds = output duration + total duration of reference videos.
        With duration = -1, billing follows the duration the model chooses. For
        current prices, see [kie.ai/pricing](https://kie.ai/pricing).

        :::


        ## Related Resources


        <CardGroup cols={2}>
          <Card title="Model Marketplace" icon="lucide-store" href="/market/quickstart">
            Explore all available models and capabilities
          </Card>
          <Card title="Common API" icon="lucide-cog" href="/common-api/get-account-credits">
            Check account credits and usage
          </Card>
        </CardGroup>
      operationId: wan-3-0-video
      tags:
        - docs/en/Market/Video Models/Wan
      parameters: []
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                model:
                  type: string
                  description: >-
                    wan/3-0-video is the standard model. The name of the model
                    used to generate videos. This field is required.


                    - This endpoint must be `wan/3-0-video`
                  enum:
                    - wan/3-0-video
                  examples:
                    - wan/3-0-video
                  x-apidog-enum:
                    - value: wan/3-0-video
                      name: ''
                      description: ''
                callBackUrl:
                  type: string
                  format: uri
                  description: >-
                    The callback URL for task completion notifications. This
                    parameter is optional. If provided, the system will send a
                    POST request to this URL when the task is completed,
                    regardless of whether it succeeds or fails. If not provided,
                    no callback notification will be sent.
                input:
                  type: object
                  description: Parameters for Wan 3.0 video generation.
                  properties:
                    prompt:
                      type: string
                      maxLength: 20000
                      description: >-
                        Text prompt, supporting both Chinese and English. Up to
                        20,000 characters. Required for text-to-video
                        generation; for other modes, it is recommended to
                        provide it together with media. In reference mode, use
                        Image1/Video1/Audio1 to reference the provided media.
                      examples:
                        - >-
                          A kitten running across a rooftop under the moonlight,
                          neon lights flickering in the distance, cinematic
                          quality, with smooth camera movement.
                    first_frame_url:
                      type: string
                      format: uri
                      description: >-
                        URL of the first-frame image. Up to 1 image, used
                        strictly as the first frame of the video. Used for
                        first-frame-to-video / first-and-last-frame-to-video
                        generation. Cannot be provided together with
                        `reference_*_urls`.

                        Formats: JPEG/JPG, PNG (transparency not supported),
                        BMP, WEBP; each side [240, 8000] px; aspect ratio ≤ 8:1;
                        ≤ 20MB.
                    last_frame_url:
                      type: string
                      format: uri
                      description: >-
                        URL of the last-frame image. Use together with
                        first_frame_url. Same format limits as first_frame_url.
                    reference_image_urls:
                      type: array
                      maxItems: 10
                      items:
                        type: string
                        format: uri
                      description: >-
                        Reference images for the all-purpose reference mode,
                        with up to 10 images. Correspond to Image1, Image2, … in
                        the prompt according to array order. Specifications are
                        the same as `first_frame_url`. Cannot be provided
                        together with the first-frame/last-frame parameters.
                    reference_video_urls:
                      type: array
                      maxItems: 5
                      items:
                        type: string
                        format: uri
                      description: >-
                        Reference videos for the all-purpose reference mode,
                        with up to 5 clips. Each clip must be 1–15 seconds, with
                        a total duration ≤ 15 seconds. Correspond to Video1,
                        Video2, … according to array order.

                        Formats: mp4, mov; each side [240, 4096] px; aspect
                        ratio ≤ 8:1; each file ≤ 100MB.

                        There is an additional constraint on the output side:
                        the input video duration + `duration` must not exceed 30
                        seconds.
                    reference_audio_urls:
                      type: array
                      maxItems: 5
                      items:
                        type: string
                        format: uri
                      description: >-
                        Reference audio for the all-purpose reference mode, with
                        up to 5 clips. Each clip must be 1–15 seconds, with a
                        total duration ≤ 15 seconds. Correspond to Audio1,
                        Audio2, … according to array order. Formats: wav, mp3; ≤
                        15MB. Reference audio can be used as the only media
                        input; pairing it with a reference image or video
                        usually gives better results.
                    reference_file_urls:
                      type: array
                      maxItems: 1
                      items:
                        type: string
                        format: uri
                      description: >-
                        File-to-video generation. Up to 1 file. Cannot be
                        provided together with `reference_link_urls`, or with
                        the first-frame/last-frame parameters.

                        Formats:
                        docx/doc/xlsx/xls/pptx/ppt/pdf/txt/key/pages/numbers/md;
                        ≤ 100MB; pdf/docx/ppt/key/pages, etc. ≤ 50 pages.
                    reference_link_urls:
                      type: array
                      maxItems: 1
                      items:
                        type: string
                        format: uri
                      description: >-
                        Link-to-video generation. Up to 1 publicly accessible
                        webpage that does not require login. Cannot be provided
                        together with `reference_file_urls`, or with the
                        first-frame/last-frame parameters.
                    resolution:
                      type: string
                      enum:
                        - 480P
                        - 720P
                        - 1080P
                      default: 1080P
                      description: 'Output resolution. Default: **1080P**.'
                    aspect_ratio:
                      type: string
                      enum:
                        - adaptive
                        - '16:9'
                        - '4:3'
                        - '1:1'
                        - '3:4'
                        - '9:16'
                      default: adaptive
                      description: >-
                        Output aspect ratio. `adaptive` (default) automatically
                        selects the ratio based on the input media and intent.
                    duration:
                      type: integer
                      default: 5
                      description: >-
                        Output video duration in seconds. Default: 5. Without
                        video input, the range is [2, 30]. With reference
                        videos: input video duration + output duration ≤ 30.
                        Pass `-1` to use an intelligent duration determined by
                        the model.
                      examples:
                        - 5
                    audio:
                      type: boolean
                      default: true
                      description: >-
                        Whether the output video includes an audio track.
                        Default: true.
                    seed:
                      type: integer
                      minimum: 0
                      maximum: 2147483647
                      description: >-
                        Random seed used to reproduce results. If omitted, a
                        random seed will be used.
                    nsfw_checker:
                      type: boolean
                      description: >-
                        Defaults to false. When false, no additional content
                        filtering is applied and results are returned directly
                        by the model.
                  x-apidog-orders:
                    - prompt
                    - first_frame_url
                    - last_frame_url
                    - reference_image_urls
                    - reference_video_urls
                    - reference_audio_urls
                    - reference_file_urls
                    - reference_link_urls
                    - resolution
                    - aspect_ratio
                    - duration
                    - audio
                    - seed
                    - 01M0SD55JHQJ1VETW8Z0B1B6S3
                  x-apidog-refs:
                    01M0SD55JHQJ1VETW8Z0B1B6S3:
                      $ref: '#/components/schemas/nsfw_checker'
                  x-apidog-ignore-properties:
                    - nsfw_checker
              x-apidog-refs: {}
              x-apidog-orders:
                - model
                - callBackUrl
                - input
              required:
                - model
                - input
              x-apidog-ignore-properties: []
            examples:
              '1':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      Under the moonlight, a little cat is running on the roof.
                      In the distance, the neon lights are flashing, giving a
                      cinematic feel. The camera movement is smooth.
                    resolution: 480P
                    aspect_ratio: adaptive
                    duration: 5
                    audio: true
                summary: Text to Video
              '2':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      The graffiti teenager emerged from the concrete wall,
                      rapping under the night-time railway bridge, with a
                      cinematic atmosphere.
                    first_frame_url: https://example.com/first-frame.png
                    resolution: 720P
                    aspect_ratio: adaptive
                    duration: 5
                    audio: true
                summary: First frame to Video
              '3':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      The young girl's expression changed from a smile to a wide
                      laugh. The camera slowly moved in, and the lighting
                      shifted from a cool tone to a warm tone.
                    first_frame_url: https://example.com/first-frame.jpg
                    last_frame_url: https://example.com/last-frame.jpg
                    resolution: 1080P
                    aspect_ratio: adaptive
                    duration: 8
                    audio: true
                    seed: 12345
                summary: First and last frames to Video
              '4':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      Image1 (a character) holds Image2 (a guitar), walks into
                      the scene shown in Video1, sits on the chair from Image3
                      and plays the song from Audio1.
                    reference_image_urls:
                      - https://example.com/character.jpg
                      - https://example.com/object.png
                      - https://example.com/prop.png
                      - https://example.com/background.png
                    reference_video_urls:
                      - https://example.com/role.mp4
                    reference_audio_urls:
                      - https://example.com/voice.mp3
                    resolution: 720P
                    aspect_ratio: adaptive
                    duration: 5
                    audio: true
                summary: Reference to Video
              '5':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      Based on this product presentation PPT, create an
                      advertisement video for an ultra-minimalist tech-style
                      smart glasses.
                    reference_file_urls:
                      - https://example.com/product.pptx
                    resolution: 480P
                    aspect_ratio: adaptive
                    duration: 10
                    audio: true
                summary: File to Video
              '6':
                value:
                  model: wan/3-0-video
                  callBackUrl: https://your-domain.com/api/callback
                  input:
                    prompt: >-
                      Based on the content of this public webpage, create a
                      concise product introduction video.
                    reference_link_urls:
                      - https://example.com/article
                    resolution: 720P
                    aspect_ratio: '16:9'
                    duration: 8
                    audio: true
                summary: Link to Video
      responses:
        '200':
          description: Request successful.
          content:
            application/json:
              schema:
                allOf:
                  - type: object
                    properties:
                      code:
                        type: integer
                        description: >-
                          Status code of the request. The HTTP status is 200
                          even when the request fails, so always check this
                          field: 200 means success; any other value means the
                          request failed and msg describes the reason. Common
                          values: 401 invalid or missing API key; 402
                          insufficient credits; 429 rate limit exceeded; 422 or
                          500 the request could not be processed (read msg and
                          check your parameters before retrying).
                        enum:
                          - 200
                          - 401
                          - 402
                          - 404
                          - 422
                          - 429
                          - 433
                          - 455
                          - 500
                          - 501
                          - 505
                      msg:
                        type: string
                        description: Response message, error description upon failure
                        examples:
                          - success
                      data:
                        type: object
                        required:
                          - taskId
                        properties:
                          taskId:
                            type: string
                            description: >-
                              The task ID can be used with the "Get Task
                              Details" endpoint to query the task status.
                            examples:
                              - dc1928bfcbc77cb6c85f3359a9c718b3
                        x-apidog-orders:
                          - taskId
                        x-apidog-ignore-properties: []
                    x-apidog-orders:
                      - 01M0SP8HE11T9EDA7PP3KQW2ZV
                    required:
                      - data
                    x-apidog-refs:
                      01M0SP8HE11T9EDA7PP3KQW2ZV:
                        $ref: '#/components/schemas/response%20not%20with%20recordId'
                    x-apidog-ignore-properties:
                      - code
                      - msg
                      - data
              example:
                code: 200
                msg: success
                data:
                  taskId: task_wan_1765180586443
          headers: {}
          x-apidog-name: ''
      security:
        - BearerAuth: []
          x-apidog:
            schemeGroups:
              - id: kn8M4YUlc5i0A0179ezwx
                schemeIds:
                  - BearerAuth
            required: true
            use:
              id: kn8M4YUlc5i0A0179ezwx
            scopes:
              kn8M4YUlc5i0A0179ezwx:
                BearerAuth: []
      callbacks:
        videoTaskCompleted:
          '{request.body#/callBackUrl}':
            post:
              description: >-
                The system sends this callback when the `wan/3-0-video` task
                succeeds or fails.
              requestBody:
                required: true
                content:
                  application/json:
                    schema:
                      type: object
                      required:
                        - code
                        - msg
                        - data
                      properties:
                        code:
                          type: integer
                          description: >-
                            Unified callback status code: 200 for success and
                            501 for failure.
                          enum:
                            - 200
                            - 501
                        msg:
                          type: string
                          description: Unified callback message.
                          enum:
                            - Playground task completed successfully.
                            - Playground task failed.
                        data:
                          type: object
                          required:
                            - taskId
                            - model
                            - state
                            - param
                          properties:
                            taskId:
                              type: string
                              description: Unique identifier of the task.
                            model:
                              type: string
                              description: The model used for the task.
                              enum:
                                - wan/3-0-video
                            state:
                              type: string
                              description: Final status of the task.
                              enum:
                                - success
                                - fail
                            param:
                              type: string
                              description: >-
                                A JSON string containing the parameters
                                submitted for the task.
                            resultJson:
                              type: string
                              nullable: true
                              description: >-
                                When the task succeeds, a JSON string containing
                                resultUrls. Present only when state is success.
                            failCode:
                              type: string
                              nullable: true
                              description: >-
                                Failure code, a numeric string such as "400" or
                                "500". Present only when state is fail.
                            failMsg:
                              type: string
                              nullable: true
                              description: Failure reason. Present only when state is fail.
                            costTime:
                              type: integer
                              format: int64
                              description: >-
                                Processing time in seconds. Present on both
                                success and failure callbacks.
                            completeTime:
                              type: integer
                              format: int64
                              description: Task completion timestamp.
                            createTime:
                              type: integer
                              format: int64
                              description: Task creation timestamp.
                            updateTime:
                              type: integer
                              format: int64
                              description: >-
                                Use completeTime as the completion time;
                                updateTime may equal createTime.
                            creditsConsumed:
                              type: number
                              description: >-
                                Credits consumed by the task; 0 for failed
                                tasks.
                    examples:
                      success:
                        summary: Task completed successfully
                        value:
                          code: 200
                          msg: Playground task completed successfully.
                          data:
                            taskId: bd8f4b1a52048d0f17a38b49591abfa1
                            model: wan/3-0-video
                            state: success
                            param: >-
                              {"input":"{\"duration\":5,\"aspect_ratio\":\"adaptive\",\"audio\":true,\"prompt\":\"Under
                              the moonlight, a little cat is running on the
                              roof. In the distance, the neon lights are
                              flashing, giving a cinematic feel. The camera
                              movement is
                              smooth.\",\"resolution\":\"480P\",\"nsfw_checker\":false}","callBackUrl":"https://webhook.uutool.cn/09df5d64-a00b-4ecf-823f-b992378a1cf3","model":"wan/3-0-video"}
                            resultJson: >-
                              {"resultUrls":["https://tempfile.aiquickdraw.com/wan30-video-alibaba/1787567408129-b1fmz5um3co.mp4"]}
                            costTime: 89
                            completeTime: 1787567409000
                            createTime: 1787567320000
                            updateTime: 1787567409000
                            creditsConsumed: 40
                      failure:
                        summary: Task failed
                        value:
                          code: 501
                          msg: Playground task failed.
                          data:
                            taskId: bd8f4b1a52048d0f17a38b49591abfa1
                            model: wan/3-0-video
                            state: fail
                            param: >-
                              {"input":"{\"duration\":5,\"aspect_ratio\":\"adaptive\",\"audio\":true,\"prompt\":\"Under
                              the moonlight, a little cat is running on the
                              roof. In the distance, the neon lights are
                              flashing, giving a cinematic feel. The camera
                              movement is
                              smooth.\",\"resolution\":\"480P\",\"nsfw_checker\":false}","callBackUrl":"https://webhook.uutool.cn/09df5d64-a00b-4ecf-823f-b992378a1cf3","model":"wan/3-0-video"}
                            failCode: '400'
                            failMsg: <error detail>
                            costTime: 89
                            creditsConsumed: 0
              responses:
                '200':
                  description: The callback was received successfully.
                  content:
                    application/json:
                      schema:
                        type: object
                        properties:
                          code:
                            type: integer
                            example: 200
                          msg:
                            type: string
                            example: success
                      example:
                        code: 200
                        msg: success
      x-apidog-folder: docs/en/Market/Video Models/Wan
      x-apidog-status: released
      x-run-in-apidog: https://app.apidog.com/web/project/1184766/apis/api-42250615-run
components:
  schemas:
    nsfw_checker:
      type: object
      properties:
        nsfw_checker:
          type: boolean
          description: >-
            Defaults to false. When false, no additional content filtering is
            applied and results are returned directly by the model.
      x-apidog-orders:
        - nsfw_checker
      x-apidog-ignore-properties: []
      x-apidog-folder: ''
    response not with recordId:
      type: object
      required:
        - data
      properties:
        code:
          type: integer
          description: >-
            Status code of the request. The HTTP status is 200 even when the
            request fails, so always check this field: 200 means success; any
            other value means the request failed and msg describes the reason.
            Common values: 401 invalid or missing API key; 402 insufficient
            credits; 429 rate limit exceeded; 422 or 500 the request could not
            be processed (read msg and check your parameters before retrying).
          enum:
            - 200
            - 401
            - 402
            - 404
            - 422
            - 429
            - 433
            - 455
            - 500
            - 501
            - 505
        msg:
          type: string
          description: Response message, error description upon failure
          examples:
            - success
        data:
          type: object
          required:
            - taskId
          properties:
            taskId:
              type: string
              description: >-
                The task ID can be used with the "Get Task Details" endpoint to
                query the task status.
              examples:
                - dc1928bfcbc77cb6c85f3359a9c718b3
          x-apidog-orders:
            - taskId
          x-apidog-ignore-properties: []
      x-apidog-orders:
        - code
        - msg
        - data
      x-apidog-ignore-properties: []
      x-apidog-folder: ''
  securitySchemes:
    BearerAuth:
      type: bearer
      scheme: bearer
      bearerFormat: API Key
      description: >-
        All API requests require a Bearer Token. Add the header `Authorization:
        Bearer YOUR_API_KEY` to authenticate requests.
    BearerAuth1:
      type: bearer
      scheme: bearer
      bearerFormat: API Key
      description: >-
        所有 API 请求都需要 Bearer Token。请在请求头中添加 `Authorization: Bearer YOUR_API_KEY`
        进行身份验证。
servers:
  - url: https://api.kie.ai
    description: 正式环境
security:
  - BearerAuth: []
    x-apidog:
      schemeGroups:
        - id: kn8M4YUlc5i0A0179ezwx
          schemeIds:
            - BearerAuth
      required: true
      use:
        id: kn8M4YUlc5i0A0179ezwx
      scopes:
        kn8M4YUlc5i0A0179ezwx:
          BearerAuth: []

```
