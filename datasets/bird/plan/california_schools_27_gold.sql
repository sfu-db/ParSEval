{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 7,
      "name": "$7",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 39,
      "name": "$39",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 36,
      "name": "$36",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 38,
      "name": "$38",
      "type": "VARCHAR"
    },
    {
      "kind": "INPUT_REF",
      "index": 40,
      "name": "$40",
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
                "index": 33,
                "name": "$33",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Monterey",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 24,
                "name": "$24",
                "type": "FLOAT"
              },
              {
                "kind": "LITERAL",
                "value": 800,
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
                "index": 9,
                "name": "$9",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "High Schools (Public)",
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