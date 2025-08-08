{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 38,
      "name": "$38",
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
                "index": 11,
                "name": "$11",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Lunch Provision 2",
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
                "index": 33,
                "name": "$33",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Merced",
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
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 16,
                    "name": "$16",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 9,
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
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 17,
                    "name": "$17",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 12,
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
                "index": 60,
                "name": "$60",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "HS",
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