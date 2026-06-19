module memwal::registry {
    use std::string::String;
    use sui::event;
    use sui::table::{Self, Table};
    public struct Registry has key {
        id: UID,
        entries: Table<String, String>,
    }
    public struct BlobRegistered has copy, drop {
        thread_id: String,
        blob_id: String,
    }
    fun init(ctx: &mut TxContext) {
        let registry = Registry {
            id: object::new(ctx),
            entries: table::new<String, String>(ctx),
        };
        transfer::share_object(registry);
    }
    entry fun register(
        registry: &mut Registry,
        thread_id: String,
        blob_id: String,
        _ctx: &mut TxContext,
    ) {
        if (table::contains(&registry.entries, thread_id)) {
            table::remove(&mut registry.entries, thread_id);
        };
        table::add(&mut registry.entries, thread_id, blob_id);
        event::emit(BlobRegistered { thread_id, blob_id });
    }
    public fun lookup(
        registry: &Registry,
        thread_id: String,
    ): (bool, String) {
        if (table::contains(&registry.entries, thread_id)) {
            let blob_id = *table::borrow(&registry.entries, thread_id);
            (true, blob_id)
        } else {
            (false, std::string::utf8(b""))
        }
    }
    #[test_only]
    use sui::test_scenario;
    #[test]
    fun test_init_creates_shared_registry() {
        let sender = @0xA;
        let mut scenario = test_scenario::begin(sender);
        {
            init(scenario.ctx());
        };
        scenario.next_tx(sender);
        {
            let registry = scenario.take_shared<Registry>();
            assert!(table::is_empty(&registry.entries));
            test_scenario::return_shared(registry);
        };
        scenario.end();
    }
    #[test]
    fun test_register_and_lookup() {
        let sender = @0xA;
        let mut scenario = test_scenario::begin(sender);
        {
            init(scenario.ctx());
        };
        scenario.next_tx(sender);
        {
            let mut registry = scenario.take_shared<Registry>();
            register(
                &mut registry,
                std::string::utf8(b"thread-001"),
                std::string::utf8(b"blob_abc"),
                scenario.ctx(),
            );
            let (found, blob_id) = lookup(&registry, std::string::utf8(b"thread-001"));
            assert!(found);
            assert!(blob_id == std::string::utf8(b"blob_abc"));
            test_scenario::return_shared(registry);
        };
        scenario.end();
    }
    #[test]
    fun test_register_overwrites_existing() {
        let sender = @0xA;
        let mut scenario = test_scenario::begin(sender);
        {
            init(scenario.ctx());
        };
        scenario.next_tx(sender);
        {
            let mut registry = scenario.take_shared<Registry>();
            register(
                &mut registry,
                std::string::utf8(b"thread-001"),
                std::string::utf8(b"blob_v1"),
                scenario.ctx(),
            );
            test_scenario::return_shared(registry);
        };
        scenario.next_tx(sender);
        {
            let mut registry = scenario.take_shared<Registry>();
            register(
                &mut registry,
                std::string::utf8(b"thread-001"),
                std::string::utf8(b"blob_v2"),
                scenario.ctx(),
            );
            let (found, blob_id) = lookup(&registry, std::string::utf8(b"thread-001"));
            assert!(found);
            assert!(blob_id == std::string::utf8(b"blob_v2"));
            test_scenario::return_shared(registry);
        };
        scenario.end();
    }
    #[test]
    fun test_lookup_missing_thread() {
        let sender = @0xA;
        let mut scenario = test_scenario::begin(sender);
        {
            init(scenario.ctx());
        };
        scenario.next_tx(sender);
        {
            let registry = scenario.take_shared<Registry>();
            let (found, blob_id) = lookup(
                &registry,
                std::string::utf8(b"nonexistent"),
            );
            assert!(!found);
            assert!(blob_id == std::string::utf8(b""));
            test_scenario::return_shared(registry);
        };
        scenario.end();
    }
    #[test]
    fun test_multiple_threads() {
        let sender = @0xA;
        let mut scenario = test_scenario::begin(sender);
        {
            init(scenario.ctx());
        };
        scenario.next_tx(sender);
        {
            let mut registry = scenario.take_shared<Registry>();
            register(
                &mut registry,
                std::string::utf8(b"thread-A"),
                std::string::utf8(b"blob_A"),
                scenario.ctx(),
            );
            register(
                &mut registry,
                std::string::utf8(b"thread-B"),
                std::string::utf8(b"blob_B"),
                scenario.ctx(),
            );
            let (found_a, id_a) = lookup(&registry, std::string::utf8(b"thread-A"));
            let (found_b, id_b) = lookup(&registry, std::string::utf8(b"thread-B"));
            let (found_c, _) = lookup(&registry, std::string::utf8(b"thread-C"));
            assert!(found_a && id_a == std::string::utf8(b"blob_A"));
            assert!(found_b && id_b == std::string::utf8(b"blob_B"));
            assert!(!found_c);
            test_scenario::return_shared(registry);
        };
        scenario.end();
    }
}
