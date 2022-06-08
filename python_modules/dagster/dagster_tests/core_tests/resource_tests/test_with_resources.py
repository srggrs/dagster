import pytest

from dagster import (
    AssetKey,
    DagsterInvalidDefinitionError,
    DagsterInvariantViolationError,
    IOManager,
    ResourceDefinition,
    io_manager,
    mem_io_manager,
    resource,
)
from dagster.core.asset_defs import AssetsDefinition, SourceAsset, asset, build_assets_job
from dagster.core.execution.with_resources import with_resources
from dagster.core.storage.mem_io_manager import InMemoryIOManager

# pylint: disable=comparison-with-callable,unbalanced-tuple-unpacking


def test_assets_direct():
    @asset(required_resource_keys={"foo"})
    def the_asset(context):
        assert context.resources.foo == "blah"
        return 5

    in_mem = InMemoryIOManager()

    @io_manager
    def the_io_manager():
        return in_mem

    transformed_asset = with_resources(
        [the_asset],
        {"foo": ResourceDefinition.hardcoded_resource("blah"), "io_manager": the_io_manager},
    )[0]
    assert isinstance(transformed_asset, AssetsDefinition)

    # When an io manager is provided with the generic key, that generic key is
    # used in the resource def dictionary.
    assert transformed_asset.node_def.output_defs[0].io_manager_key == "io_manager"

    assert build_assets_job("the_job", [transformed_asset]).execute_in_process().success
    assert list(in_mem.values.values())[0] == 5


def test_asset_requires_io_manager_key():
    @asset(io_manager_key="the_manager")
    def the_asset():
        return 5

    in_mem = InMemoryIOManager()

    @io_manager
    def the_io_manager():
        return in_mem

    transformed_asset = with_resources(
        [the_asset],
        {"foo": ResourceDefinition.hardcoded_resource("blah"), "the_manager": the_io_manager},
    )[0]
    assert isinstance(transformed_asset, AssetsDefinition)

    assert build_assets_job("the_job", [transformed_asset]).execute_in_process().success
    assert list(in_mem.values.values())[0] == 5


def test_assets_direct_resource_conflicts():
    @asset(required_resource_keys={"foo"})
    def the_asset():
        pass

    @asset(required_resource_keys={"foo"})
    def other_asset():
        pass

    transformed_asset = with_resources(
        [the_asset],
        {"foo": ResourceDefinition.hardcoded_resource("blah")},
    )[0]

    other_transformed_asset = with_resources(
        [other_asset],
        {"foo": ResourceDefinition.hardcoded_resource("blah")},
    )[0]

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="Conflicting versions of resource with key 'foo' were provided to different assets. When constructing a job, all resource definitions provided to assets must match by reference equality for a given key.",
    ):
        build_assets_job("the_job", [transformed_asset, other_transformed_asset])


def test_source_assets_no_key_provided():
    class MyIOManager(IOManager):
        def handle_output(self, context, obj):
            pass

        def load_input(self, context):
            return 5

    @io_manager
    def the_manager():
        return MyIOManager()

    my_source_asset = SourceAsset(key=AssetKey("my_source_asset"))

    @asset
    def my_derived_asset(my_source_asset):
        return my_source_asset + 4

    transformed_source, transformed_derived = with_resources(
        [my_source_asset, my_derived_asset], resource_defs={"io_manager": the_manager}
    )

    # When an io manager definition is provided using the generic key, that
    # generic key is used as the io manager key for the source asset.
    assert transformed_source.get_io_manager_key() == "io_manager"

    the_job = build_assets_job("the_job", [transformed_derived], source_assets=[transformed_source])

    result = the_job.execute_in_process()
    assert result.success
    assert result.output_for_node("my_derived_asset") == 9


def test_source_assets_key_provided():
    class MyIOManager(IOManager):
        def handle_output(self, context, obj):
            pass

        def load_input(self, context):
            return 5

    @io_manager
    def the_manager():
        return MyIOManager()

    my_source_asset = SourceAsset(key=AssetKey("my_source_asset"), io_manager_key="the_manager")

    @asset
    def my_derived_asset(my_source_asset):
        return my_source_asset + 4

    transformed_source, transformed_derived = with_resources(
        [my_source_asset, my_derived_asset], resource_defs={"the_manager": the_manager}
    )

    # When an io manager definition is provided using the generic key, that
    # generic key is used as the io manager key for the source asset.
    assert transformed_source.get_io_manager_key() == "the_manager"

    the_job = build_assets_job("the_job", [transformed_derived], source_assets=[transformed_source])

    result = the_job.execute_in_process()
    assert result.success
    assert result.output_for_node("my_derived_asset") == 9


def test_source_assets_manager_def_provided():
    class MyIOManager(IOManager):
        def handle_output(self, context, obj):
            pass

        def load_input(self, context):
            return 5

    @io_manager
    def the_manager():
        return MyIOManager()

    my_source_asset = SourceAsset(key=AssetKey("my_source_asset"), io_manager_def=the_manager)

    @asset
    def my_derived_asset(my_source_asset):
        return my_source_asset + 4

    transformed_source, transformed_derived = with_resources(
        [my_source_asset, my_derived_asset], resource_defs={"io_manager": mem_io_manager}
    )

    # When an io manager definition has already been provided, it will use an
    # override key.
    assert transformed_source.io_manager_def == the_manager

    the_job = build_assets_job("the_job", [transformed_derived], source_assets=[transformed_source])

    result = the_job.execute_in_process()
    assert result.success
    assert result.output_for_node("my_derived_asset") == 9


def test_asset_def_partial_application():
    @asset(required_resource_keys={"foo", "bar"})
    def the_asset():
        pass

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="resource with key 'bar' required by op 'the_asset' was not provided.",
    ):
        with_resources([the_asset], {"foo": ResourceDefinition.hardcoded_resource("foo")})


def test_source_asset_no_manager_def():
    the_source_asset = SourceAsset(key=AssetKey("my_source_asset"))
    # Ensure we don't error when no manager def is provided for default key,
    # because io manager def can utilize default.
    with_resources([the_source_asset], {})

    the_source_asset = SourceAsset(key=AssetKey("my_source_asset"), io_manager_key="foo")
    # Ensure error when io manager key is provided and resources don't satisfy.
    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="requires IO manager with key 'foo', but none was provided.",
    ):
        with_resources([the_source_asset], {})


def test_asset_transitive_resource_deps():
    @resource(required_resource_keys={"foo"})
    def the_resource(context):
        assert context.resources.foo == "bar"

    @asset(resource_defs={"the_resource": the_resource})
    def the_asset():
        pass

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="resource with key 'foo' required by resource with key 'the_resource' was not provided",
    ):
        with_resources([the_asset], {})

    transformed_asset = with_resources(
        [the_asset], {"foo": ResourceDefinition.hardcoded_resource("bar")}
    )[0]

    assert build_assets_job("blah", [transformed_asset]).execute_in_process().success


def test_asset_io_manager_transitive_dependencies():
    class MyIOManager(IOManager):
        def handle_output(self, context, obj):
            pass

        def load_input(self, context):
            return 5

    @io_manager(required_resource_keys={"the_resource"})
    def the_manager():
        return MyIOManager()

    @asset(io_manager_def=the_manager)
    def the_asset():
        pass

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="resource with key 'the_resource' required by resource with key 'the_asset__io_manager' was not provided.",
    ):
        with_resources([the_asset], resource_defs={})

    @resource(required_resource_keys={"foo"})
    def the_resource(context):
        assert context.resources.foo == "bar"

    with pytest.raises(
        DagsterInvariantViolationError,
        match="Resource with key 'foo' required by resource with key 'the_resource', but not provided.",
    ):
        with_resources([the_asset], resource_defs={"the_resource": the_resource})

    transformed_assets = with_resources(
        [the_asset],
        resource_defs={
            "the_resource": the_resource,
            "foo": ResourceDefinition.hardcoded_resource("bar"),
        },
    )
    assert build_assets_job("blah", transformed_assets).execute_in_process().success


def test_source_asset_partial_resources():
    class MyIOManager(IOManager):
        def handle_output(self, context, obj):
            pass

        def load_input(self, context):
            return 5

    @io_manager(required_resource_keys={"foo"})
    def the_manager(context):
        assert context.resources.foo == "blah"
        return MyIOManager()

    my_source_asset = SourceAsset(key=AssetKey("my_source_asset"), io_manager_def=the_manager)

    with pytest.raises(
        DagsterInvariantViolationError,
        match="Resource with key 'foo' required by resource with key 'my_source_asset__io_manager', but not provided.",
    ):
        with_resources([my_source_asset], resource_defs={})

    @resource(required_resource_keys={"bar"})
    def foo_resource(context):
        return context.resources.bar

    with pytest.raises(
        DagsterInvariantViolationError,
        match="Resource with key 'bar' required by resource with key 'foo', but not provided.",
    ):
        with_resources([my_source_asset], resource_defs={"foo": foo_resource})

    transformed_source = with_resources(
        [my_source_asset],
        resource_defs={"foo": foo_resource, "bar": ResourceDefinition.hardcoded_resource("blah")},
    )[0]

    @asset
    def my_derived_asset(my_source_asset):
        return my_source_asset + 4

    the_job = build_assets_job("the_job", [my_derived_asset], source_assets=[transformed_source])

    result = the_job.execute_in_process()
    assert result.success
    assert result.output_for_node("my_derived_asset") == 9


def test_asset_circular_resource_dependency():
    @asset(required_resource_keys={"foo"})
    def the_asset():
        pass

    @resource(required_resource_keys={"bar"})
    def foo():
        pass

    @resource(required_resource_keys={"foo"})
    def bar():
        pass

    with pytest.raises(
        DagsterInvariantViolationError, match='Resource key "bar" transitively depends on itself.'
    ):
        with_resources([the_asset], resource_defs={"foo": foo, "bar": bar})