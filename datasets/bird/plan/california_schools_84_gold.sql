{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$1"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 38,
          "name": "$38",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 29,
          "name": "$29",
          "type": "VARCHAR"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 65,
                    "name": "$65",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 1,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
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
                    "index": 62,
                    "name": "$62",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "K-8",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
                    "index": 11,
                    "name": "$11",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Multiple Provision Types",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
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
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 29,
                    "name": "$29",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "frpm",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
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