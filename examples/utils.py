from pydantic_ai.agent import Agent


DEFAULT_MODEL_NAME = "google/gemini-2.5-flash"


async def chatify(agent: Agent):
    result = None
    while True:
        # Call a tool on the server
        message_history = []
        if result:
            message_history = result.all_messages()

        user_multiline_input = None
        print("[USER]: \n")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                return "<EXIT>"
            if line.strip() == "!":
                break
            if line.strip() in ["q!", "qq", "qqq", ":q"]:
                print("Exiting program.")
                return "<EXIT>"
            lines.append(line)
        user_multiline_input = "\n".join(lines)

        result = await agent.run(user_multiline_input, message_history=message_history)
        print(result.output)
        print()
        print()
