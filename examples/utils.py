from pydantic_ai.agent import Agent


DEFAULT_MODEL_NAME = "google/gemini-2.5-flash-preview-05-20"


async def chatify(agent: Agent):
        result = None
        while True:
            # Call a tool on the server
            message_history = []
            if result:
                message_history = result.all_messages()

            user_multiline_input = None
            print("[USER]: \n")
            while True:
                line = input()
                line = line.strip()
                if line == "!":
                    break
                if line == "q!":
                    import sys

                    sys.exit(0)
                if user_multiline_input:
                    user_multiline_input = f"""
{user_multiline_input}
{line}
                    """
                else:
                    user_multiline_input = line

            result = await agent.run(
                user_multiline_input, message_history=message_history
            )
            print(result.output)
            print()
            print()
