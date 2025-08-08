{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 13,
                "name": "$13",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "DC Comics",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 8,
                    "name": "$8",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 12,
                    "name": "$12",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "superhero",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "publisher",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}