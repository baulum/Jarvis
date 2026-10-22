# GPT Image 2.5 Sunburst - Image To Image

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
      summary: GPT Image 2.5 Sunburst - Image To Image
      deprecated: false
      description: >-
        ## Create Task


        Use this endpoint to create a new image-to-image generation task.


        <Card title="Get Task Details" icon="lucide-search"
        href="/market/common/get-task-detail">
          After submission, use the unified query endpoint to check task progress and retrieve results
        </Card>


        ::: tip[]

        For production use, we recommend providing the `callBackUrl` parameter
        so your service can receive completion notifications instead of polling
        for task status.

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
      operationId: gpt-image-2-5-sunburst-image-to-image
      tags:
        - docs/en/Market/Image    Models/GPT Image
      parameters: []
      requestBody:
        content:
          application/json:
            schema:
              properties:
                model:
                  default: gpt-image-2-5-sunburst-image-to-image
                  description: >-
                    The model name used for generation. This field is required.
                    This endpoint must use the
                    `gpt-image-2-5-sunburst-image-to-image` model.
                  enum:
                    - gpt-image-2-5-sunburst-image-to-image
                  examples:
                    - gpt-image-2-5-sunburst-image-to-image
                  type: string
                callBackUrl:
                  description: >-
                    Callback URL for task completion notifications. Optional
                    parameter. If provided, the system will send a POST request
                    to this URL when the task completes, whether it succeeds or
                    fails. If omitted, no callback notification will be sent. To
                    ensure callback security, see [Webhook Verification
                    Guide](/common-api/webhook-verification).
                  examples:
                    - https://your-domain.com/api/callback
                  format: uri
                  type: string
                input:
                  description: Input parameters for the image-to-image task.
                  properties:
                    prompt:
                      description: Text prompts, up to 20,000 characters.
                      examples:
                        - >-
                          Transform this product image into a premium e-commerce
                          poster style.
                      type: string
                    input_urls:
                      description: Array of input image URLs.
                      examples:
                        - - https://example.com/
                      items:
                        format: uri
                        type: string
                      maxItems: 16
                      type: array
                    aspect_ratio:
                      description: >-
                        The aspect ratio of the generated image is set to auto
                        by default. The 27:16, 16:27, 9:8 and 8:9 aspect ratios
                        support 1K only. 2K and 4K are available for other
                        aspect ratios.
                      enum:
                        - auto
                        - '1:1'
                        - '3:2'
                        - '2:3'
                        - '4:3'
                        - '3:4'
                        - '16:9'
                        - '9:16'
                        - '21:9'
                        - '27:16'
                        - '16:27'
                        - '9:8'
                        - '8:9'
                      type: string
                      x-apidog-enum:
                        - description: ''
                          label: auto
                          value: auto
                        - description: ''
                          label: '1:1'
                          value: '1:1'
                        - description: ''
                          label: '3:2'
                          value: '3:2'
                        - description: ''
                          label: '2:3'
                          value: '2:3'
                        - description: ''
                          label: '4:3'
                          value: '4:3'
                        - description: ''
                          label: '3:4'
                          value: '3:4'
                        - description: ''
                          label: '5:4'
                          value: '5:4'
                        - description: ''
                          label: '4:5'
                          value: '4:5'
                        - description: ''
                          label: '16:9'
                          value: '16:9'
                        - description: ''
                          label: '9:16'
                          value: '9:16'
                        - description: ''
                          label: '2:1'
                          value: '2:1'
                        - description: ''
                          label: '1:2'
                          value: '1:2'
                        - description: ''
                          label: '3:1'
                          value: '3:1'
                        - description: ''
                          label: '1:3'
                          value: '1:3'
                        - description: ''
                          label: '21:9'
                          value: '21:9'
                        - description: ''
                          name: ''
                          value: '9:21'
                    resolution:
                      description: >-
                        Image resolution. The background parameter is only
                        available at 1K.
                      enum:
                        - 1K
                        - 2K
                        - 4K
                      type: string
                      x-apidog-enum:
                        - description: ''
                          name: ''
                          value: 1K
                        - description: ''
                          name: ''
                          value: 2K
                        - description: ''
                          name: ''
                          value: 4K
                    background:
                      description: >-
                        Image background

                        Note: This parameter is only supported when the
                        resolution is 1K.
                      enum:
                        - transparent
                        - opaque
                        - auto
                      type: string
                      x-apidog-enum:
                        - description: ''
                          name: Transparency
                          value: transparent
                        - description: ''
                          name: Opaque
                          value: opaque
                        - description: ''
                          name: Automatically
                          value: auto
                  required:
                    - prompt
                  type: object
                  x-apidog-orders:
                    - prompt
                    - input_urls
                    - aspect_ratio
                    - resolution
                    - background
                  x-apidog-ignore-properties: []
              required:
                - model
                - input
              type: object
              x-apidog-orders:
                - model
                - callBackUrl
                - input
              x-apidog-ignore-properties: []
            example:
              model: gpt-image-2-5-sunburst-image-to-image
              callBackUrl: https://your-domain.com/api/callback
              input:
                prompt: >-
                  Transform this product image into a premium e-commerce poster
                  style.
                input_urls:
                  - https://example.com/
                aspect_ratio: auto
                resolution: 1K
                background: opaque
      responses:
        '200':
          description: Request successful
          content:
            application/json:
              schema:
                allOf:
                  - properties:
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
                    required:
                      - data
                    type: object
                    x-apidog-orders:
                      - 01KPR08ETH0VJCQNFC9R5B1DJ7
                    x-apidog-refs:
                      01KPR08ETH0VJCQNFC9R5B1DJ7:
                        $ref: '#/components/schemas/response%20not%20with%20recordId'
                    x-apidog-ignore-properties:
                      - code
                      - msg
                      - data
              example:
                code: 200
                msg: success
                data:
                  taskId: task_gptimage_1765180586443
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
        onImageGenerated:
          '{$request.body#/callBackUrl}':
            post:
              summary: Image Generation Callback
              description: Sent when the task succeeds or fails.
              requestBody:
                required: true
                content:
                  application/json:
                    schema:
                      type: object
                      properties:
                        code:
                          type: integer
                          description: >-
                            200: success. 501: failure; see data.failCode and
                            data.failMsg.
                          enum:
                            - 200
                            - 501
                        msg:
                          type: string
                          description: Status message
                          example: Playground task completed successfully.
                        data:
                          type: object
                          properties:
                            completeTime:
                              type: integer
                              format: int64
                              description: >-
                                Task completion time, represented as a Unix
                                timestamp in milliseconds
                              example: 1786433042000
                            costTime:
                              type: integer
                              description: >-
                                Processing time in seconds. Present on both
                                success and failure callbacks.
                              example: 78
                            createTime:
                              type: integer
                              format: int64
                              description: >-
                                Task creation time, represented as a Unix
                                timestamp in milliseconds
                              example: 1786432963000
                            creditsConsumed:
                              type: number
                              format: double
                              description: >-
                                Credits consumed by the task; 0 for failed
                                tasks.
                              example: 3
                            model:
                              type: string
                              description: Image editing model used for the task
                              example: gpt-image-2-5-sunburst-image-to-image
                            param:
                              type: string
                              description: >-
                                Parameters submitted when creating the task, in
                                JSON string format
                              example: >-
                                {"input":"{\"aspect_ratio\":\"auto\",\"prompt\":\"take
                                a photo with Sam Altman in the conference
                                room\",\"input_urls\":[\"https://static.aiquickdraw.com/tools/example/1776782793756_wrogXTdd.png\"]}","callBackUrl":"https://your-domain.com/api/callback","model":"gpt-image-2-5-sunburst-image-to-image"}
                            resultJson:
                              type: string
                              description: >-
                                Image generation result in JSON string format.
                                resultUrls contains the list of generated image
                                URLs. Present only when state is success.
                              example: >-
                                {"resultUrls":["https://tempfile.aiquickdraw.com/images/chatgpt/file_000000008b8481f7a0a8618ee2d10b84.png"]}
                            state:
                              type: string
                              description: Task status
                              enum:
                                - success
                                - fail
                              example: success
                            taskId:
                              type: string
                              description: Task ID
                              example: 3fd37da5c726d0c8b1278f391906ac82
                            updateTime:
                              type: integer
                              format: int64
                              description: >-
                                Use completeTime as the completion time;
                                updateTime may equal createTime.
                              example: 1786433042000
                            failCode:
                              type: string
                              description: >-
                                Failure code, a numeric string such as "400" or
                                "500". Present only when state is fail.
                              example: '500'
                            failMsg:
                              type: string
                              description: Failure reason. Present only when state is fail.
                              example: <error detail>
                    examples:
                      success:
                        summary: Success
                        value:
                          code: 200
                          msg: Playground task completed successfully.
                          data:
                            taskId: 3fd37da5c726d0c8b1278f391906ac82
                            model: gpt-image-2-5-sunburst-image-to-image
                            state: success
                            param: >-
                              {"input":"{\"aspect_ratio\":\"auto\",\"prompt\":\"take
                              a photo with Sam Altman in the conference
                              room\",\"input_urls\":[\"https://static.aiquickdraw.com/tools/example/1776782793756_wrogXTdd.png\"]}","callBackUrl":"https://your-domain.com/api/callback","model":"gpt-image-2-5-sunburst-image-to-image"}
                            resultJson: >-
                              {"resultUrls":["https://tempfile.aiquickdraw.com/images/chatgpt/file_000000008b8481f7a0a8618ee2d10b84.png"]}
                            completeTime: 1786433042000
                            createTime: 1786432963000
                            costTime: 78
                            creditsConsumed: 3
                            updateTime: 1786433042000
                      failure:
                        summary: Failure
                        value:
                          code: 501
                          msg: Playground task failed.
                          data:
                            taskId: 3fd37da5c726d0c8b1278f391906ac82
                            model: gpt-image-2-5-sunburst-image-to-image
                            state: fail
                            param: >-
                              {"input":"{\"aspect_ratio\":\"auto\",\"prompt\":\"take
                              a photo with Sam Altman in the conference
                              room\",\"input_urls\":[\"https://static.aiquickdraw.com/tools/example/1776782793756_wrogXTdd.png\"]}","callBackUrl":"https://your-domain.com/api/callback","model":"gpt-image-2-5-sunburst-image-to-image"}
                            failCode: '500'
                            failMsg: <error detail>
                            completeTime: 1786433042000
                            createTime: 1786432963000
                            costTime: 78
                            creditsConsumed: 0
                            updateTime: 1786433042000
              responses:
                '200':
                  description: Callback received successfully
      x-apidog-folder: docs/en/Market/Image    Models/GPT Image
      x-apidog-status: released
      x-run-in-apidog: https://app.apidog.com/web/project/1184766/apis/api-43286923-run
components:
  schemas:
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
