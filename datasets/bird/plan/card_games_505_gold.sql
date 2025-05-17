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
  "id": "6",
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
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "IN",
                "operator": "IN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 62,
                    "name": "$62",
                    "type": "VARCHAR"
                  }
                ],
                "query": [
                  {
                    "relOp": "LogicalProject",
                    "project": [
                      {
                        "kind": "INPUT_REF",
                        "index": 4,
                        "name": "$4",
                        "type": "VARCHAR"
                      }
                    ],
                    "id": "2",
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
                              "index": 15,
                              "name": "$15",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "World Championship Decks 2004",
                              "type": "VARCHAR",
                              "nullable": false,
                              "precision": -1
                            }
                          ]
                        },
                        "variableset": "[]",
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "sets",
                            "id": "0",
                            "inputs": []
                          }
                        ]
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 10,
                    "name": "$10",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 3.0,
                    "type": "FLOAT",
                    "nullable": false,
                    "precision": 15
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}