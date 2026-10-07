from worlds.LauncherComponents import Component, Type, components, launch


def run_client(*args: str) -> None:
    # Imported here, not at the top: the client pulls in CommonClient, which generation never needs.
    from .client.context import launch as launch_client

    launch(launch_client, name="BioShock Client", args=args)


components.append(
    Component(
        "BioShock Client",
        func=run_client,
        game_name="BioShock",
        component_type=Type.CLIENT,
        supports_uri=True,
        description="Connects BioShock Remastered to Archipelago.",
    )
)
